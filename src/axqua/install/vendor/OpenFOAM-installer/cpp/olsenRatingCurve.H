/*
 * Optional quasi-steady Q->z extension for Olsen's serial sediDriftFoam2.
 * Copyright (C) 2026 sediment-installer contributors.
 * SPDX-License-Identifier: GPL-3.0-or-later
 * This is NOT BAW's VOF p_rgh boundary condition and is not a transient ALE fix.
 */
#ifndef olsenRatingCurve_H
#define olsenRatingCurve_H

#include "IOdictionary.H"
#include "Pstream.H"
#include "Switch.H"
#include "Tuple2.H"
#include "boolList.H"
#include <cmath>

class OlsenRatingCurve
{
    const Foam::fvMesh& mesh_;
    bool enabled_;
    Foam::label outletID_;
    Foam::label referenceCell_;
    Foam::scalar relaxation_;
    Foam::scalar gravity_;
    Foam::scalar minDepth_;
    Foam::scalar minStage_;
    Foam::word bounds_;
    Foam::List<Foam::Tuple2<Foam::scalar, Foam::scalar>> table_;
    Foam::boolList outletSurface_;
    Foam::List<Foam::Tuple2<Foam::label, Foam::label>> columns_;

    static bool finite(const Foam::scalar value) { return std::isfinite(value); }

public:
    OlsenRatingCurve
    (
        const Foam::fvMesh& mesh,
        const Foam::Time& runTime,
        const Foam::surfaceScalarField& phi,
        const Foam::volScalarField& p
    )
    :
        mesh_(mesh), enabled_(false), outletID_(-1), referenceCell_(-1),
        relaxation_(0.1), gravity_(9.81), minDepth_(0.001),
        minStage_(-Foam::GREAT), bounds_("error")
    {
        using namespace Foam;
        IOdictionary config
        (
            IOobject
            (
                "ratingCurveProperties", runTime.constant(), mesh,
                IOobject::READ_IF_PRESENT, IOobject::NO_WRITE
            )
        );
        enabled_ = config.lookupOrDefault<Switch>("enabled", false);
        if (!enabled_) return;

        if (Pstream::parRun())
        {
            FatalErrorInFunction << "Rating-curve extension is serial only." << exit(FatalError);
        }
        if (phi.dimensions() != dimVolume/dimTime || p.dimensions() != sqr(dimVelocity))
        {
            FatalErrorInFunction
                << "Expected volumetric phi [m3/s] and kinematic p [m2/s2]."
                << exit(FatalError);
        }
        const word outlet(config.lookupOrDefault<word>("outletPatch", "outletWater"));
        outletID_ = mesh.boundaryMesh().findPatchID(outlet);
        const label bedID = mesh.boundaryMesh().findPatchID("bedWall");
        const label surfaceID = mesh.boundaryMesh().findPatchID("freeSurface");
        if (outletID_ < 0 || bedID < 0 || surfaceID < 0)
        {
            FatalErrorInFunction << "Need outletPatch, bedWall and freeSurface patches." << exit(FatalError);
        }
        if
        (
            p.boundaryField()[outletID_].type() != "fixedValue"
         || p.boundaryField()[surfaceID].type() != "zeroGradient"
        )
        {
            FatalErrorInFunction
                << "Rating path requires Olsen's fixedValue outlet p and zeroGradient freeSurface p. "
                << "BAW p_rgh conditions cannot be substituted here." << exit(FatalError);
        }
        relaxation_ = config.lookupOrDefault<scalar>("relaxation", 0.1);
        gravity_ = config.lookupOrDefault<scalar>("gravity", 9.81);
        minDepth_ = config.lookupOrDefault<scalar>("minDepth", 0.001);
        minStage_ = config.lookupOrDefault<scalar>("minStageElevation", -GREAT);
        bounds_ = config.lookupOrDefault<word>("outOfBounds", "error");
        if
        (
            !finite(relaxation_) || relaxation_ <= 0 || relaxation_ > 1
         || !finite(gravity_) || gravity_ <= 0
         || !finite(minDepth_) || minDepth_ <= 0 || !finite(minStage_)
         || (bounds_ != "error" && bounds_ != "clamp")
        )
        {
            FatalErrorInFunction << "Invalid relaxation, gravity, minDepth, minStageElevation or outOfBounds." << exit(FatalError);
        }
        config.lookup("table") >> table_;
        if (table_.size() < 2)
        {
            FatalErrorInFunction << "table needs at least two (Q eta) pairs." << exit(FatalError);
        }
        forAll(table_, i)
        {
            const scalar q = table_[i].first(), eta = table_[i].second();
            if
            (
                !finite(q) || !finite(eta) || q < 0 || eta < minStage_
             || (i > 0 && (q <= table_[i-1].first() || eta < table_[i-1].second()))
            )
            {
                FatalErrorInFunction
                    << "Use finite nonnegative Q in strictly increasing order, "
                    << "nondecreasing stages above minStageElevation." << exit(FatalError);
            }
        }

        const fvPatch& bed = mesh.boundary()[bedID];
        const fvPatch& surface = mesh.boundary()[surfaceID];
        if (bed.size() == 0 || bed.size() != surface.size())
        {
            FatalErrorInFunction << "bedWall/freeSurface must have equal nonzero face counts." << exit(FatalError);
        }
        boolList bedPoints(mesh.nPoints(), false), surfacePoints(mesh.nPoints(), false);
        boolList outletPoints(mesh.nPoints(), false);
        const fvPatch& outletPatch = mesh.boundary()[outletID_];
        forAll(outletPatch, i)
        {
            const face& f = mesh.faces()[outletPatch.start()+i];
            forAll(f, v) outletPoints[f[v]] = true;
        }
        forAll(bed, i)
        {
            const face& b = mesh.faces()[bed.start()+i];
            const face& s = mesh.faces()[surface.start()+i];
            if (b.size() != 4 || s.size() != 4)
            {
                FatalErrorInFunction << "Olsen requires quadrilateral bed/surface faces." << exit(FatalError);
            }
            forAll(b, v) bedPoints[b[v]] = true;
            forAll(s, v) surfacePoints[s[v]] = true;
        }
        forAll(mesh.cells(), c)
        {
            const cell& cc = mesh.cells()[c];
            if (cc.size() != 6)
            {
                FatalErrorInFunction << "Olsen requires six-face hexahedral cells." << exit(FatalError);
            }
            forAll(cc, f)
            {
                if (mesh.faces()[cc[f]].size() != 4)
                {
                    FatalErrorInFunction << "Olsen requires quadrilateral cell faces." << exit(FatalError);
                }
            }
        }
        outletSurface_.setSize(mesh.nPoints(), false);
        label outletSurfaceCount = 0, bedCount = 0;
        forAll(bedPoints, i)
        {
            if (bedPoints[i] && surfacePoints[i])
            {
                FatalErrorInFunction << "A point cannot belong to both bed and surface." << exit(FatalError);
            }
            if (bedPoints[i]) ++bedCount;
            outletSurface_[i] = surfacePoints[i] && outletPoints[i];
            if (outletSurface_[i]) ++outletSurfaceCount;
        }
        if (outletSurfaceCount < 2)
        {
            FatalErrorInFunction << "outletPatch must share the downstream edge with freeSurface." << exit(FatalError);
        }
        // Choose an ACTUAL outlet-adjacent surface cell, not an arbitrary last face.
        forAll(surface, i)
        {
            const face& f = mesh.faces()[surface.start()+i];
            forAll(f, v)
            {
                if (outletSurface_[f[v]]) referenceCell_ = surface.faceCells()[i];
            }
        }
        columns_.setSize(bedCount);
        label col = 0;
        boolList coveredSurface(mesh.nPoints(), false);
        forAll(bedPoints, b)
        {
            if (!bedPoints[b]) continue;
            label s = -1;
            for (label k = b+1; k < mesh.nPoints() && k < b+1000; ++k)
            {
                if (bedPoints[k]) break;
                if (surfacePoints[k]) { s = k; break; }
            }
            if (s < 0)
            {
                FatalErrorInFunction
                    << "Point numbering must be contiguous bottom-to-top columns <1000 points. "
                    << "Failed at bed point " << b << exit(FatalError);
            }
            const point& bp = mesh.points()[b];
            const scalar tolerance = 1e-8*max(scalar(1), mag(mesh.points()[s]-bp));
            for (label k = b; k <= s; ++k)
            {
                const point& pt = mesh.points()[k];
                if (mag(vector(pt.x()-bp.x(), pt.y()-bp.y(), 0)) > tolerance)
                {
                    FatalErrorInFunction << "Numbered columns must be vertical (z is up)." << exit(FatalError);
                }
            }
            if (coveredSurface[s])
            {
                FatalErrorInFunction << "Two bed columns share a surface endpoint." << exit(FatalError);
            }
            coveredSurface[s] = true;
            columns_[col++] = Tuple2<label,label>(b,s);
        }
        forAll(surfacePoints, i)
        {
            if (surfacePoints[i] && !coveredSurface[i])
            {
                FatalErrorInFunction << "Unpaired freeSurface point " << i << exit(FatalError);
            }
        }
        // The upstream algorithm pairs faces by index: verify their x/y footprints.
        forAll(bed, i)
        {
            const face& b = mesh.faces()[bed.start()+i];
            const face& s = mesh.faces()[surface.start()+i];
            forAll(b, v)
            {
                bool found = false;
                forAll(s, w)
                {
                    const point& bp = mesh.points()[b[v]];
                    const point& sp = mesh.points()[s[w]];
                    if (mag(vector(bp.x()-sp.x(),bp.y()-sp.y(),0)) < 1e-8) found = true;
                }
                if (!found)
                {
                    FatalErrorInFunction << "bedWall/freeSurface faces must correspond by array index." << exit(FatalError);
                }
            }
        }
        validateMoved(mesh.points());
        Info<< "Olsen geometric rating curve enabled: outlet=" << outlet
            << ", referenceCell=" << referenceCell_ << ", relaxation=" << relaxation_ << nl;
    }

    bool enabled() const { return enabled_; }
    Foam::label referenceCell() const { return referenceCell_; }
    Foam::scalar relaxation() const { return relaxation_; }
    Foam::scalar gravity() const { return gravity_; }

    Foam::scalar stage(const Foam::surfaceScalarField& phi) const
    {
        using namespace Foam;
        const scalar q = sum(phi.boundaryField()[outletID_]);
        if (!finite(q) || q < 0)
        {
            FatalErrorInFunction << "Rating requires finite outward Q >= 0; got " << q
                << ". Reverse flow is unsupported." << exit(FatalError);
        }
        scalar eta = 0;
        if (q < table_.first().first() || q > table_.last().first())
        {
            if (bounds_ == "error")
            {
                FatalErrorInFunction << "Q=" << q << " is outside rating table ["
                    << table_.first().first() << ',' << table_.last().first() << "]."
                    << exit(FatalError);
            }
            eta = q < table_.first().first() ? table_.first().second() : table_.last().second();
            WarningInFunction << "Clamping out-of-range Q=" << q << " to endpoint stage " << eta << nl;
        }
        else
        {
            label upper = 1;
            while (upper < table_.size()-1 && q > table_[upper].first()) ++upper;
            const scalar weight = (q-table_[upper-1].first())/(table_[upper].first()-table_[upper-1].first());
            eta = (1-weight)*table_[upper-1].second() + weight*table_[upper].second();
        }
        forAll(columns_, i)
        {
            const label b = columns_[i].first(), s = columns_[i].second();
            if (outletSurface_[s] && eta < mesh_.points()[b].z() + minDepth_)
            {
                FatalErrorInFunction << "Rating target " << eta
                    << " would dry an outlet column; wetting/drying is unsupported."
                    << exit(FatalError);
            }
        }
        Info<< "ratingCurve Qout=" << q << " m3/s etaTarget=" << eta << " m" << nl;
        return eta;
    }

    void enforceOutlet
    (
        Foam::pointField& moved,
        const Foam::pointField& current,
        const Foam::scalar eta
    ) const
    {
        forAll(outletSurface_, i)
        {
            if (outletSurface_[i]) moved[i].z() = current[i].z() + relaxation_*(eta-current[i].z());
        }
    }

    void validateMoved(const Foam::pointField& moved) const
    {
        using namespace Foam;
        forAll(moved, i)
        {
            if (!finite(moved[i].x()) || !finite(moved[i].y()) || !finite(moved[i].z()))
            {
                FatalErrorInFunction << "Nonfinite mesh point " << i << exit(FatalError);
            }
        }
        forAll(columns_, i)
        {
            const label b = columns_[i].first(), s = columns_[i].second();
            if (moved[s].z()-moved[b].z() < minDepth_ || moved[s].z() < minStage_)
            {
                FatalErrorInFunction << "Rating motion would violate minDepth/minStageElevation in column "
                    << b << ". No artificial erosion is performed to repair it." << exit(FatalError);
            }
            for (label k = b+1; k <= s; ++k)
            {
                if (moved[k].z() <= moved[k-1].z())
                {
                    FatalErrorInFunction << "Inverted/non-increasing vertical column at point " << k << exit(FatalError);
                }
            }
        }
    }
};

#endif
