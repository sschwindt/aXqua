I did some editing revisions in the docs that must be preserved and require further actions in the docs and beyond; namely, please implement the following:

* not use "river reach" -- this is way to much confining because aXqua should also enable to look at local hydraulic structures (especially through openfoam) and lakes and reservoirs, so better use "free-surface water bodies" (or suggest a better name)

* note that the docs are now hosted through my modified setting in `docs/config.py` (and other platform admin stuff) at `https://axqua.eu`

* use American English everywhere in this project

* in the Configuration tab:
	- the *Simulation software* box also contains postprocessors, so rename this box to **Simulation & postprocessing software**
	- make found installation paths dark green and the "Not found on this computer." text dark-purple (aXqua icon color toward the bottom-right) bold; if one presses the *Install [...]* button for a software where the path already exists, show a pop-up "Software already installed. Continue with re-installation?" (Yes - No buttons --> "No" go back to Configuration tab; "Yes" -- go through the wizard an provide reinstallation options.


* in the installation wizards, enable users to switch between the *Settings*, *Check*, and *Installation* tabs, so make those tabs, not pages

* in the Telemac installation wizard, *Settings* tab (page), modify *Input files only* to *Input files of examples only*.

* under the *SALOME arhive* box, correct: "[...] aXqua **uses Gmsh and** does not need SALOME." At least I hope my Gmsh assumption is correct. If so, cite accordingly in the docs: "Geuzaine, C., Remacle, J.-F., 2009. Gmsh: A 3-D finite element mesh generator with built-in pre- and post-processing facilities. International Journal for Numerical Methods in Engineering 79, 1309–1331. https://doi.org/10.1002/nme.2579"

* in the installation wizards, *Check* tab (page):
	- make the box that raises required terminal actions by the user a little larger, make the text color in the box dark purple (aXqua icon color toward the bottom-right), and place above that box in bold capital-lettered same dark-purple-colored font: **REQUIRED USER ACTIONS in TERMINAL:**;
	- then place a `Run fixes` button below that box that executes the proposed code and automatically re-checks and runs if necessary the fixes;
	- if the checks and fixes finally do not work out (e.g. error message keeps being the same and cannot be fixed, there should be a `Report issue` button that opens a new issue on GitHub, possibly with the user not needing a new account



* on my laptop (Linux Mint 22.3, x86_64), I ran through the OpenFOAM installation, did what was stated in the *Check* tab but the installation ended up in the error message ```foam-build /home/schwindt/.local/openfoam-sediment-v2406/src/OpenFOAM-v2406/etc/bashrc /home/schwindt/.local/openfoam-sediment-v2406 7 /bin/bash -c . ./AllWmake
OF environment found.
Compiling ...
ERROR: Smoke-run directory already exists; preserve it and choose a new prefix```

* After the installation, if it was succssful, the *Cancel the installation* button at the bottom of the *Installation* frame should turn dark-gray green and its text switch to *Installation complete*

* in the QGIS aXqua plugin menu, already the top-menu should show the aXqua icon; at the next level, the panel should also keep the aXqua icon, the settings should get a cogwheel icon

* enable aXqua to set up OpenFOAM simulations with the sediment transport solvers (Morphodynamic simulation tab bounded OpenFOAM option) or with the stage-discharge boundary (Hydraulic simulation-tab-bounded OpenFOAM Setup)

* in the aXqua panel, *Case Setup* tab, the **Project file** frame is confusing because with the current configuration, a project is a case; so if there is anything relevant in the project settings for the considered cases, port those settings over to the case and rename **Project file** to **Case settings** where an *Open case settings...* button opens a GUI where users can then graphically select case settings, select files, reasonable values from drop down menus etc; correct the docs and the wizard accordingly
