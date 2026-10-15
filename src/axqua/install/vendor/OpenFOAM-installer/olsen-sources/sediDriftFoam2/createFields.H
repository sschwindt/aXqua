Info<< "Reading field p\n" << endl;
volScalarField p
(
    IOobject
    (
        "p",
        runTime.timeName(),
        mesh,
        IOobject::MUST_READ,
        IOobject::AUTO_WRITE
    ),
    mesh
);

Info<< "Reading field U\n" << endl;
volVectorField U
(
    IOobject
    (
        "U",
        runTime.timeName(),
        mesh,
        IOobject::MUST_READ,
        IOobject::AUTO_WRITE
    ),
    mesh
);

Info<< "Reading field Umod\n" << endl;
volVectorField Umod
(
    IOobject
    (
        "Umod",
        runTime.timeName(),
        mesh,
        IOobject::MUST_READ,
        IOobject::NO_WRITE
    ),
    mesh
);


Info<< "Reading field nut\n" << endl;
volScalarField nut
(
	IOobject
	(
	"nut",
	runTime.timeName(),
	mesh,
	IOobject::MUST_READ,
	IOobject::AUTO_WRITE
	),
	mesh
);


Info<< "Reading field Conc\n" << endl;
volScalarField Conc
(
	IOobject
	(
	"Conc",
	runTime.timeName(),
	mesh,
	IOobject::NO_READ,
	IOobject::AUTO_WRITE
	),
	mesh
);

// source terms

volScalarField S_implicit
(
	IOobject
	(
	"S_implicit",
	runTime.timeName(),
	mesh,
	IOobject::NO_READ,
	IOobject::NO_WRITE
	),
	mesh
);


volScalarField S_explicit
(
	IOobject
	(
	"S_explicit",
	runTime.timeName(),
	mesh,
	IOobject::NO_READ,
	IOobject::NO_WRITE
	),
	mesh
);


IOdictionary sedimentProperties
(
	IOobject
	(
	"sedimentProperties",
	runTime.constant(),
	mesh,
	IOobject::MUST_READ,
	IOobject::NO_WRITE
	)
);


#include "createPhi.H"
#include "createPhiC.H"

label pRefCell = 0;
scalar pRefValue = 0.0;
setRefCell(p, simple.dict(), pRefCell, pRefValue);
mesh.setFluxRequired(p.name());


singlePhaseTransportModel laminarTransport(U, phi);

autoPtr<incompressible::turbulenceModel> turbulence
(
    incompressible::turbulenceModel::New(U, phi, laminarTransport)
);

#include "createMRF.H"
#include "createFvOptions.H"

