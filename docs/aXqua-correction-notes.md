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

* in the installation wizards, *Installation* tab (page): After the installation, if it was succssful, the *Cancel the installation* button at the bottom of the *Installation* frame should turn dark-gray green and its text switch to *Installation complete*

* in the QGIS aXqua plugin menu, already the top-menu should show the aXqua icon; at the next level, the panel should also keep the aXqua icon, the settings should get a cogwheel icon

* enable aXqua to set up OpenFOAM simulations with the sediment transport solvers (Morphodynamic simulation tab bounded OpenFOAM option) or with the stage-discharge boundary (Hydraulic simulation-tab-bounded OpenFOAM Setup)

* in the aXqua panel, *Case Setup* tab, the **Project file** frame is confusing because with the current configuration, a project is a case; so if there is anything relevant in the project settings for the considered cases, port those settings over to the case and rename **Project file** to **Case settings** where an *Open case settings...* button opens a GUI where users can then graphically select case settings, select files, reasonable values from drop down menus etc; correct the docs and the wizard accordingly

* in the *Case Setup* tab, *Edit case...* button and menu, mark all required items dark-purple (axqua) purple bold for a minimum use-case; in that wizard window, also fix wording:
	- Rename "Project paths" to "Project settings" (because the EPSG number is not a path)
	- "Boundaries" > "Liquid boundaries"
	- "Initialization" > "Initial conditions"
	- Clarify why the "drying of thin film" option matter in an information bubble in that tab page
	- "Hydraulic simulation" should be followed by "Morphodynamic simulation"
	- the "TELEMAC" and "OpenFOAM" tab pages are unequal: OpenFOAM contains also mesh settings that should rather be derived from the "Mesh configuration" tab page
	- "Structures" and "Surface from CAD" must be merged as frames into the "OpenFOAM tab page"
	- rename "Calibration" tab page to "Calibration and validation"
	- Merge "Ground truth data" into "Calibration and validation" and add options for what shall be used for calibration and what for validation
	- rename "Figures" into "Post-processing" and not only add options for Telemac velocity and depth plots but also OpenFOAM post-processing output for Visit-DAV and ParaView

* In the axqua panel, move the *Mesh convergence* tab into the *Hydraulic simulations* tab, and let user choose to either run the mesh convergence check with Telemac or OpenFOAM; so this might require some new tweaking of the backend to also enable mesh convergence studies with OpenFOAM

* Rename *Hydraulic simulations* to *Simulations* and merge the *Morphodynamic simulation* main tab into that tab, so that users can choose to either use Telemac of OpenFOAM for morphodynamic simulations; then in the *Simulations* tab, rename the *Telemac* tab to *Baseline (Telemac)*; keep the *OpenFOAM* named tab.

* in general, mark all required user input items in dark-purple (axqua purple) color; if a user checks a new option like OpenFOAM or Morphodynamics or unsteady etc. -- the required items should turn dark-axqua-purple, too then

* the *Help* button on the top right corner should refer to the online help on axqua.readtheocs.io (that will soon be replaced with axqua.eu)
