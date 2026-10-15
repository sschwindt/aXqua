/*---------------------------------------------------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     | Website:  https://openfoam.org
    \\  /    A nd           | Copyright (C) 2011-2018 OpenFOAM Foundation
     \\/     M anipulation  | Copyright (C) 2022-2025 Nils Reidar B. Olsen
-------------------------------------------------------------------------------
License
    This file is part of OpenFOAM.

    OpenFOAM is free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OpenFOAM is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
    FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
    for more details.

    You should have received a copy of the GNU General Public License
    along with OpenFOAM.  If not, see <http://www.gnu.org/licenses/>.

Application
    sediDriftFoam2

Description
    Sediment transport solver for incompressible, turbulent flow, based on the 
    simpleFoam solver, with bed changes and an adaptive grid. Note that the order
    of the points must be increasing in the vertical direction and the cells must 
    be hexahedral. This can be done by making the grid with SSIIM 2, using 
    F 488 2 and F 87 0.0001 in the control file and using the polyMesh files, 
    instead of the blockMesh file. Afterwards, renumberMesh has to be used. 
    Rename the /0/ directory temporarily if you get error meassages in renumberMesh.

\*---------------------------------------------------------------------------*/

#include "fvCFD.H"
#include "singlePhaseTransportModel.H"
#include "turbulentTransportModel.H"
#include "simpleControl.H"
#include "fvOptions.H"
#include "kEpsilon.H"
#include "math.h"
#include "Time.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

int main(int argc, char *argv[])
{
    #include "postProcess.H"
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    #include "createControl.H"
    #include "createFields.H"

    #include "initContinuityErrs.H"
    #include "fvmSup.H"

    turbulence->validate();

    // * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

    scalar particleSize(readScalar(sedimentProperties.lookup("particleSize")));
    scalar fallVelocity(readScalar(sedimentProperties.lookup("fallVelocity")));
    scalar roughness(readScalar(sedimentProperties.lookup("roughness")));
    scalar timeStepSediments(readScalar(sedimentProperties.lookup("timeStepSediments")));
    scalar waterRelax(readScalar(sedimentProperties.lookup("waterRelax")));
    scalar waterSmoother(readScalar(sedimentProperties.lookup("waterSmoother")));
    scalar conversionCoefficient(readScalar(sedimentProperties.lookup("conversionCoefficient")));
    scalar minDampingFactor(readScalar(sedimentProperties.lookup("minDampingFactor")));
    scalar tanAngleRepose(readScalar(sedimentProperties.lookup("tanAngleRepose")));
    scalar cosAngleRepose(readScalar(sedimentProperties.lookup("cosAngleRepose")));
    int initialIterations(readInt(sedimentProperties.lookup("initialIterations")));
    int sedimentFormula(readInt(sedimentProperties.lookup("sedimentFormula")));
    int loopSlideMax(readInt(sedimentProperties.lookup("loopSlideMax")));  // maximum loops for slide algorithm 
    int variableTimeStepNumber(readInt(sedimentProperties.lookup("variableTimeStepNumber")));
    static float updateinterval = 1.0; // updates grid every updateinterval time step. floating point number used in the fmod function 
    static float loopnumber = 0.0; 
    static float minwaterlevel = 0.15; // minium water level in meters
    static float minDepthMultiplier = 10.0;
    static float sumMoveBed = 0.0; //
    static double sedimentTime = 0.0; 
    static scalar maxX = -1.0e20;
   
    FILE * scour;

    if((scour = fopen("scour.txt","w")) == NULL) {
	Info << "Error opening file 'scour' " << endl;
	}

    fprintf(scour," Iteration   Time (hours)   Max scour depth (m) \n");

    label bedPatchID = mesh.boundaryMesh().findPatchID("bedWall");
    const fvPatch& patchBed = mesh.boundary()[bedPatchID];
    label startBedPatch = patchBed.start(); // first face in the bed patch.
    label sizeBedPatch = patchBed.size(); 
    const pointField& points = mesh.points();
    label nPoints = mesh.points().size();
    label nCells = mesh.cells().size();
    
    scalarField kineticenergy(nCells);

    pointField oldpoints(nPoints);
    pointField newpoints(nPoints);
    newpoints = points;  // C++ automatic loop
    oldpoints = points;  // C++ automatic loop
    labelField surfNorth(sizeBedPatch);
    labelField surfSouth(sizeBedPatch);
    labelField surfEast(sizeBedPatch);
    labelField surfWest(sizeBedPatch);
    labelField surfNo(sizeBedPatch);
    scalarField vxArray(sizeBedPatch);
    scalarField vyArray(sizeBedPatch);
    scalarField vzArray(sizeBedPatch);

    surfNorth = 0;
    surfSouth = 0;
    surfEast = 0;
    surfWest = 0;
    surfNo = 0;
    label countNb = 0;

///////// structure for smoothing of bed surface vectors to avoid bed oscillations  /////////////////////////////////////////////////

    forAll(patchBed,bed) {   
	forAll(patchBed,bed2) {
		if(mesh.faces()[startBedPatch+bed][3] == mesh.faces()[startBedPatch+bed2][0] 
		&& mesh.faces()[startBedPatch+bed][2] == mesh.faces()[startBedPatch+bed2][1]) {
			surfEast[bed] = bed2;
			surfWest[bed2] = bed;
			surfNo[bed]++;
			surfNo[bed2]++;				
			countNb++;
			}
		if(mesh.faces()[startBedPatch+bed][1] == mesh.faces()[startBedPatch+bed2][0] 
		&& mesh.faces()[startBedPatch+bed][2] == mesh.faces()[startBedPatch+bed2][3]) {
			surfNorth[bed] = bed2;
			surfSouth[bed2] = bed;
			surfNo[bed]++;
			surfNo[bed2]++;
			countNb++;
			}
		}
	}

    Info << "\n Grid size: " << nCells << " points: " << nPoints << " bed surfaces: " << sizeBedPatch << " bed cell neighbours: " << countNb << endl;

    vectorField bedCenterG = patchBed.Cf();
    forAll(patchBed,bedg) {   
	if(bedCenterG[bedg].x() > maxX) maxX = bedCenterG[bedg].x(); 
	}

    Info<< "\nStarting time loop\n" << endl;

////////////////////////// here the main time loop starts /////////////////////////////////////////////////////////////////////////////

    while (simple.loop(runTime))   // if you get a compile error for this line, try the line below instead - it has to do with different OpenFOAM versions 
//    while (simple.loop())   // if you get a compile error for this line, try the line above instead - it has to do with different OpenFOAM versions 
    {
        Info<< "Time = " << runTime.timeName() << nl << endl;

	loopnumber += 1.0;

        // --- Pressure-velocity SIMPLE corrector
        {
            #include "UEqn.H"
            #include "pEqn.H"
        }

        laminarTransport.correct();
        turbulence->correct();

	// drift-flux velocities   

        forAll(Umod, i) {
		Umod[i].x() = 0.0;
		Umod[i].y() = 0.0;
		Umod[i].z() = fallVelocity;
             	}

	phi_c = linearInterpolate(Umod) & mesh.Sf(); 
	phi_d = phi + phi_c;

	forAll(S_implicit,j) {
		S_implicit[j] = 0.0;
		S_explicit[j] = 0.0;
		}

	// surface: implicit term

	label surfPatchID = mesh.boundaryMesh().findPatchID("freeSurface");
	const fvPatch& patchSurf = mesh.boundary()[surfPatchID];

	forAll(patchSurf,i) {
		label cellnumber = patchSurf.faceCells()[i];
		S_implicit[cellnumber] = -fallVelocity * patchSurf.magSf()[i] / mesh.V()[cellnumber];  // units 1/sec
		}

	// bed: explicit term

	scalar dist_to_wall = 0.5 * mesh.V()[0] / patchBed.magSf()[0]; 
	vectorField bedNormal = patchBed.nf();
	vectorField bedCenter = patchBed.Cf();
       
/////// computing pick-up rate: S_explicit   ///////////////////////////////////////////////////////////////////////////////////////

	forAll(patchBed,bed) {
		label cellbed = patchBed.faceCells()[bed];

/////////	computing bed shear stress and critical bed shear stress according to Shields

		dist_to_wall = mesh.C()[cellbed].z() - bedCenter[bed].z(); // 
		scalar bedVelocity = mag(U[cellbed]);  // no compile errors
		scalar shearVelocity = 0.4 * bedVelocity / Foam::log(30.0 * dist_to_wall / roughness);  // log-law gives shear velocity
		scalar bedShear = 1000.0 * shearVelocity * shearVelocity;  // shearVelocity to bed shear stress
		scalar particleReynolds = shearVelocity * particleSize * 1.0e6;
		scalar ShieldsCoefficient = 0.06;
		scalar alpha = 0.0;
		if(particleReynolds < 0.2) ShieldsCoefficient = 0.6;
		else if (particleReynolds < 500.0) {   // Shields curve
			alpha = Foam::log10(particleReynolds);
			alpha = -0.99863612 - 0.9253539586 * alpha + 0.54283631 * alpha * alpha - 0.084544063 * alpha * alpha * alpha;
			ShieldsCoefficient = Foam::pow(10.0,alpha);
			}
		scalar criticalShear = ShieldsCoefficient * 1650.0 * 9.81 * particleSize; // Shields formula

/////////	Dey formula reduction critical shear stress on sloping beds   /////////////////////////////////////////////////////

		scalar Ux = U[cellbed].x();
		scalar Uy = U[cellbed].y();
		scalar vx = 0.0001;
		scalar vy = 0.0;
		scalar vz = 1.0;
		if(surfNo[bed] == 4) {
			#include "computevx3.h"
		} else {
			#include "computevx4.h"
		}
		scalar Bx = vx;
		scalar By = vy;
		scalar Bz = vz;
		scalar horizontalVelocity = Foam::sqrt(Ux*Ux+Uy*Uy) + 1.0e-20;
		scalar bedVector = Foam::sqrt(Bx*Bx + By*By) + 1.0e-20;		
		Ux /= horizontalVelocity;
		Uy /= horizontalVelocity;
		Bx /= bedVector;
		By /= bedVector;
		scalar velocityDotBed = Bx * Ux + By * Uy;
		scalar velocityCrossBed = Bx * Uy - By * Ux;
		if(velocityCrossBed < 0.0) velocityCrossBed = - velocityCrossBed; // absolute value in formula
		scalar normalbz = Bz;
		scalar bedSlope = Foam::acos(normalbz); // always positive, normalbz is always positive
		scalar modifiedTanAngleRepose = tanAngleRepose;
		scalar criticalBedSlope = Foam::atan(modifiedTanAngleRepose); // angle of repose for bed material 
		scalar termTheta = bedSlope * velocityDotBed / criticalBedSlope;  // negative for uphill flow
		if(termTheta > 0.99) termTheta = 0.99;
		if(termTheta < -0.99) termTheta = -0.99;
		scalar termAlpha = bedSlope * velocityCrossBed / criticalBedSlope;  // always positive
		if(termAlpha > 0.99) termAlpha = 0.99;
		scalar dampingFactor = 0.945 * Foam::pow(1.0 - termTheta, 0.745) * Foam::pow(1.0 - termAlpha, 0.372);  // Dey
		if(dampingFactor < minDampingFactor) dampingFactor = minDampingFactor;
		if(dampingFactor > 3.0) dampingFactor = 3.0;
		if(bedCenter[bed].x() < 0.5 && dampingFactor < 1.0) dampingFactor = 1.0;  // stabilize inflow region
		if(bedCenter[bed].x() < 0.5) dampingFactor += 10.0 * (0.5 - bedCenter[bed].x());  // stabilize inflow region
		criticalShear *= dampingFactor; 
			
//////////      vanRijns sediment transport formulas    ///////////////////////////////////////////////////////////////////////////////////

		scalar Tstar = (bedShear - criticalShear) / criticalShear;  // parameter in van Rijns formula
		if(Tstar < 0.0) Tstar = 0.0; // in case of negative Tstar
		scalar Dstar = particleSize * 25296.0;  // parameter in van Rijns formula
		scalar vanRijn = 0.0;
		if(sedimentFormula == 1) { // van Rijns bedload formula
			vanRijn = 0.053 * Foam::pow(Tstar,2.1) / Foam::pow(Dstar,0.3) * Foam::pow(particleSize,1.5) * 4.02;  // bed load concentration according to van Rijn (1982), m2/s
			vanRijn /= (2.0 * dist_to_wall * bedVelocity);  // concentration = qs/qw [m2/s / m2/s]
		} else if (sedimentFormula == 0) { // van Rijns suspended load formula
			vanRijn = 0.015 * particleSize / dist_to_wall * Foam::pow(Tstar, 1.5) / Foam::pow(Dstar,0.3); 
		}
		if(vanRijn > 0.1) vanRijn = 0.1; // max concentration
		S_explicit[cellbed] = -fallVelocity / (2.0 * dist_to_wall) * vanRijn;  // 1/second: 29/3-25 changed -= to = -
		}

////////// solve the equation for sediment concentration  /////////////////////////////////////////////////////////////////////////////////////

	solve
	(
	fvm::ddt(Conc)
	+ fvm::div(phi_d, Conc)  
        + fvm::Sp(S_implicit,Conc)  
	== 
	S_explicit
	+ fvm::laplacian(turbulence->nut(), Conc)
	);

////////////////////// change the mesh: /////////////////////////////////////////////////////////////////////////////////////////////

	if(fmod(loopnumber,updateinterval) < 0.0001 && loopnumber > initialIterations) {

	scalarField boundaryMovePoints(nPoints);
	labelField pointMove(nPoints);

	forAll(pointMove,pi) pointMove[pi] = 0;  
	forAll(boundaryMovePoints,pi) boundaryMovePoints[pi] = 0.0;  

	scalar erosionDepth = 10000.0; // large positive number
	label startSurfPatch = patchSurf.start(); // first face in the surface patch.
	label noSurfaces = patchSurf.size();

////////////// computes changes in bed and surface elevations /////////////////////////////////////////////////////////////////////////////

	label pointatbed[4];
	label pointatsurf[4];
	label refcell = patchSurf.faceCells()[noSurfaces-1];
	double surflevel = 0.0;
	for(int a=0;a<4;a++) {
		surflevel += points[mesh.faces()[startSurfPatch+noSurfaces-1][a]].z();
		}
	surflevel *= 0.25;

	scalar timeStepCtemp = timeStepSediments;
	if(loopnumber < initialIterations * variableTimeStepNumber) timeStepCtemp *= static_cast<float>(loopnumber - initialIterations) / static_cast<float>((variableTimeStepNumber-1)* initialIterations);  // variable time step
	sedimentTime += timeStepCtemp;
	dist_to_wall = 0.5 * mesh.V()[0] / patchBed.magSf()[0]; 

	forAll(patchBed,bed) {
		label cellbed = patchBed.faceCells()[bed];
		label cellsurf = patchSurf.faceCells()[bed];	
		dist_to_wall = mesh.C()[cellbed].z() - bedCenter[bed].z(); // 
		double deltaMoveBed = (Conc[cellbed] * (-fallVelocity) -  S_explicit[cellbed] * 2.0 * dist_to_wall) * timeStepCtemp * conversionCoefficient;
		if(bedCenter[bed].x() > maxX - 1.0) {  // 13/5-25
			scalar ratioX = maxX - bedCenter[bed].x();  // between 0 and 1
			if(ratioX < 0.0) ratioX = 0.0;
    			if(loopnumber == initialIterations+1) {
				Info<< "Test loop, bed = " << bed << " ratioX = " << ratioX << " deltaMoveBed = " << deltaMoveBed << endl;
				}
			deltaMoveBed *= ratioX;
			}
		sumMoveBed += deltaMoveBed * patchBed.magSf()[bed]; // moved volume
		double levelsurfpatch = 0.0;
		double levelbedpatch = 0.0;
		for(int b=0;b<4;b++) {
			levelsurfpatch += points[mesh.faces()[bed+startSurfPatch][b]].z();
			levelbedpatch += points[mesh.faces()[bed+startBedPatch][b]].z();
			}
		levelsurfpatch *= 0.25;
		levelbedpatch *= 0.25;
		double deltaMoveSurf = ((p[cellsurf] -  p[refcell]) / 9.81 + surflevel - levelsurfpatch) * waterRelax;  
		if(deltaMoveSurf + levelsurfpatch < minwaterlevel) deltaMoveSurf = minwaterlevel - levelsurfpatch; // hydraulic jump can not be modelled with this method. Pressure losses are overpredicted.
		for(int i=0;i<4;i++) {
			int i2;
			if(i==1) i2 = 3; else if (i==3) i2 = 1; else i2 = i; // bed and surface patch point numbers rotate in different directions
			pointatbed[i] = mesh.faces()[startBedPatch+bed][i];
			pointatsurf[i] = mesh.faces()[startSurfPatch+bed][i2];
			pointMove[pointatbed[i]] += 1;
			pointMove[pointatsurf[i]] -= 1;
			boundaryMovePoints[pointatbed[i]] += deltaMoveBed; 
			boundaryMovePoints[pointatsurf[i]] += (1.0 - waterSmoother) * deltaMoveSurf + waterSmoother * (levelsurfpatch - newpoints[pointatsurf[i]].z());
			}
		}  // endfor patch bed

////// transfer movements to the newpoints array   ////////////////////////////////////////////////////////////////////////////////////////////////

	int bedpi = 0;
	double deltab = 0.0;
	scalar dist_to_wall0 = mesh.V()[0] / patchBed.magSf()[0]; // heigth of cell 0 
	forAll(newpoints,pi) {
	   if(pointMove[pi] > 0) {  // bed point
		deltab = boundaryMovePoints[pi] / static_cast<double>(pointMove[pi]);
		newpoints[pi].z() += deltab;
		if(newpoints[pi].x() < -0.01 && newpoints[pi].z() < 0.0) newpoints[pi].z() = 0.0; // fix the upstream bed level to maintain constant water discharge. 
		bedpi = pi;   // b1 is now the bed point for the next if-sentence
		}
	   if(pointMove[pi] < 0) {  // surface point
		double deltas = boundaryMovePoints[pi] / static_cast<double>(-pointMove[pi]);
		newpoints[pi].z() += deltas;
		if(newpoints[pi].z() < newpoints[bedpi].z() + dist_to_wall0 * minDepthMultiplier) {
			newpoints[bedpi].z() = newpoints[pi].z() - dist_to_wall0 * minDepthMultiplier; // minimum water depth: dist_to_wall
			}
		}
	} // endfor pi

//////////  sand slide algorithm ////////////////////////////////////////////////////////////////////////////////////////////////

	int loopSlide;
	for(loopSlide = 0; loopSlide < loopSlideMax; loopSlide++) {

	label outOfLoop = 1;

	forAll(patchBed,bed) {  // bed starts at zero
	   #include "computevx.h"
	} // endfor patchBed

	for(int bed = sizeBedPatch-1; bed >= 0; bed--) {  // other direction
	   #include "computevx.h"
	} // endfor patchBed

	if(outOfLoop == 1) break;  // jumps out of slide loop if the bed is ok
	} // endfor loop slide

	Info << " loopSlide: " << loopSlide << endl;

/////////////////////////////////// compute changes in the grid points between the bed and the water surface ///////////////////////////////////////////

	label maxErosionPoint = 0;
	forAll(newpoints,pi) {
	   if(pointMove[pi] > 0) {  // bed
		if(newpoints[pi].z() < erosionDepth) {
			erosionDepth = newpoints[pi].z();
			maxErosionPoint = pi;
			}
		for(int n=1;n<1000;n++) {  // max 1000 points between bed and surface
			int s = pi + n;
			if(pointMove[s] < 0) {  // surface
				double waterdepth = newpoints[s].z() - newpoints[pi].z();
				double deltaz = waterdepth / static_cast<double>(n);
				for(int l=pi+1;l<s;l++) {
					newpoints[l].z() = static_cast<double>(l-pi) * deltaz + newpoints[pi].z();
					} // endfor l
				n = 10000;
				} // endif
			} // endfor n
		} // endif pointMove
	    } // endfor newpoints, pi

////////////////////// updates the grid ////////////////////////////////////////////////////////////////////////////////

	mesh.movePoints(newpoints);

	Info << "Erosion depth: " << erosionDepth << " meters, point " << maxErosionPoint << " bedMove: " << sumMoveBed << " m3 " << " sedimentTime " << sedimentTime/3600.0 << " hours " << endl; 
	fprintf(scour,"%10.0lf %lf %lf %lf\n",loopnumber, sedimentTime/3600.0, timeStepCtemp, erosionDepth);

        } // end move grid

////////// print-out ///////////////////////////////////////////////////////////////////////////////////////////////////

        runTime.write();

        Info<< "ExecutionTime = " << runTime.elapsedCpuTime() << " s"
            << "  ClockTime = " << runTime.elapsedClockTime() << " s"
            << nl << endl;

	if(Conc[0] != Conc[0]) break; // test for nan, stops run if crash

    }  // end time step loop

    Info<< "End\n" << endl;

    fclose(scour);

    return 0;
}


// ************************************************************************* //
