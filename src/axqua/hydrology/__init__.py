"""Hydrology mode: rainfall-runoff over a catchment with TELEMAC-2D.

A catchment case differs from a river reach in what drives it and what bounds it. Rain falls on every node, part of it infiltrates after Green-Ampt, part of the surface water evaporates, and the rest runs off to an outlet; the outline is the drainage divide, which is a solid wall by definition, and the outlet is the one place water leaves.

The modules of this package are the parts that do not depend on TELEMAC:

* :mod:`~axqua.hydrology.greenampt` - the three infiltration kernels as a reference in Python, the same algorithm the generated Fortran routine runs per node;
* :mod:`~axqua.hydrology.rainfall` - reading a rain series and building design storms;
* :mod:`~axqua.hydrology.weather` - wind components and evaporation (FAO-56), and the forcing tables TELEMAC reads;
* :mod:`~axqua.hydrology.soil` - Green-Ampt parameters by texture and per node;
* :mod:`~axqua.hydrology.terrain` - flow directions on the DEM, for the checkup;
* :mod:`~axqua.hydrology.checks` - the hydrology checkup as findings;
* :mod:`~axqua.hydrology.report` - outlet hydrograph and water balance of a run;
* :mod:`~axqua.hydrology.portals` and :mod:`~axqua.hydrology.fetch` - where data come from, and the downloads the data wizard offers.

What TELEMAC needs (the generated ``runoff_greenampt.f``, the steering keywords) is in :mod:`axqua.solvers.telemac.hydrology` and :mod:`axqua.solvers.telemac.greenampt`.
"""
