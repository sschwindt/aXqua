"""Installing the simulation programs: TELEMAC, OpenFOAM, ParaView and VisIt.

aXqua does not carry installers of its own. It runs the published installer scripts
(:data:`axqua.install.recipes.REPOSITORY`) at the version it was tested with, and adds
what an installation started by a click needs: it detects the operating system
(:mod:`.host`), says beforehand what will happen and what is missing (:mod:`.recipes`),
runs the installation as a process that outlives the window that started it, and enters
the result in the profile of this computer (:mod:`.runner`).

``axqua install`` is the command (:mod:`axqua.installcli`); the installation wizards of
the QGIS plugin call it.
"""
