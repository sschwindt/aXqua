/*---------------------------------------------------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     | Website:  https://openfoam.org
    \\  /    A nd           | Copyright (C) 2011-2018 OpenFOAM Foundation
     \\/     M anipulation  | Copyright (C) 2022 Nils Reidar B. Olsen
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
    ssiimFoam

Description
    Sediment transport solver for incompressible, turbulent flow, based on the 
    simpleFoam solver.

\*---------------------------------------------------------------------------*/

#include "fvCFD.H"
#include "singlePhaseTransportModel.H"
#include "turbulentTransportModel.H"
#include "simpleControl.H"
#include "fvOptions.H"
#include "kEpsilon.H"

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

    scalar particlesize(readScalar(sedimentProperties.lookup("particlesize")));
    scalar fallvelocity(readScalar(sedimentProperties.lookup("fallvelocity")));
    scalar roughness(readScalar(sedimentProperties.lookup("roughness")));

    Info<< "\nStarting time loop\n" << endl;

    while (simple.loop(runTime))
    {
        Info<< "Time = " << runTime.timeName() << nl << endl;

        // --- Pressure-velocity SIMPLE corrector
        {
            #include "UEqn.H"
            #include "pEqn.H"
        }

        laminarTransport.correct();
        turbulence->correct();

	eddyvisc = turbulence->nut();  // the variable nut decleared in 
	kineticenergy = turbulence->k(); // ??

	// sediment concentrations   

        forAll(Umod, i) {
		Umod[i].x() = 0.0;
		Umod[i].y() = 0.0;
		Umod[i].z() = fallvelocity;
             	}

	phi_c = linearInterpolate(Umod) & mesh.Sf(); 
	phi_d = phi + phi_c;

	forAll(S_implicit,j) {
		S_implicit[j] = 0.0;
		S_explicit[j] = 0.0;
		}

	// surface: implicit term

	label surfPatchID = mesh.boundaryMesh().findPatchID("freeSurface");
	if(surfPatchID < 0) {  // is this correct??
        	Info<< "Have not found any 'freeSurface' patches in the grid" << endl;  // error message
		}
	const fvPatch& patchSurf = mesh.boundary()[surfPatchID];
	forAll(patchSurf,k) {
		label cellnumber = patchSurf.faceCells()[k];
		S_implicit[cellnumber] = -fallvelocity * patchSurf.magSf()[k] / mesh.V()[cellnumber];  // units 1/sec
		}

	// bed: explicit term

	label bedPatchID = mesh.boundaryMesh().findPatchID("bedWall");
	if(bedPatchID < 0) {
        	Info<< "Have not found any 'bottomWall' patches in the grid" << endl;  // error message
		}
	const fvPatch& patchBed = mesh.boundary()[bedPatchID];

	forAll(patchBed,bed) {
		label cellbed = patchBed.faceCells()[bed];
		scalar dist_to_wall = 0.5 * mesh.V()[cellbed] / patchBed.magSf()[bed]; // in lack of another working option 
		scalar bedvelocity = mag(U[cellbed]);  // no compile errors
		scalar bedshear2 = 0.4 * bedvelocity / Foam::log(30.0 * dist_to_wall / roughness);  // log-law
		bedshear2 = 1000.0 * bedshear2 * bedshear2;  // shearvelocity to bed shear stress
		scalar bedshear = 300.0 * kineticenergy[cellbed]; // from SSIIM
		scalar critshear = 0.047 * 1650.0 * 9.81 * particlesize; // Shields
		scalar Tstar = (bedshear2 - critshear) / critshear;  // parameter in van Rijns formula
		if(Tstar < 0.0) Tstar = 0.0; // in case of negative Tstar
		scalar Dstar = particlesize * 25296.0;  // parameter in van Rijns formula
		scalar vanRijn = 0.015 * particlesize / dist_to_wall * Foam::pow(Tstar, 1.5) / Foam::pow(Dstar,0.3);  // concentration according to van Rijn

// pick-up rate for concrete bed, where sediment erosion is limited

		if(fallvelocity > 0) { // plastic
			S_implicit[cellbed] = fallvelocity / (2.0 * dist_to_wall);  // 1/second: 
		} else if(bedshear > critshear) {  // settling particles
			if(vanRijn > Conc[cellbed]) {
				S_explicit[cellbed] -= fallvelocity / (2.0 * dist_to_wall) * Conc[cellbed];  // 1/second: 
			} else {
				S_explicit[cellbed] -= fallvelocity / (2.0 * dist_to_wall) * vanRijn;  // 1/second: 
				}
			}
		}

	// solve the equation

	solve
	(
	fvm::ddt(Conc)
	+ fvm::div(phi_d, Conc)  
        + fvm::Sp(S_implicit,Conc)  
	== 
	S_explicit
	+ fvm::laplacian(turbulence->nut(), Conc)
	);

//    change the mesh: just started

/*
	const pointField& points = mesh.points();

	forAll(points,p) 
	{
		// change the z values of the points according to erosion/deposition
	}

	mesh.movePoints(points);
*/
	// print-out
        runTime.write();

        Info<< "ExecutionTime = " << runTime.elapsedCpuTime() << " s"
            << "  ClockTime = " << runTime.elapsedClockTime() << " s"
            << nl << endl;
    }

    Info<< "End\n" << endl;

    return 0;
}

/*
bool Foam::dynamicFvMeshS::update()
{

	pointField newPoints;

	fvMesh::movePoints(newPoints);

	Info<<"Updating mesh"<< endl; 

	return true;

}
*/

// ************************************************************************* //
