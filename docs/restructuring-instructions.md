description: 
"""
- this document explains how to restructure the documentation for read the docs, and if needed, the entire package, too
- `#` decribe in python-style comments that should not appear in the docs structure but they described what should go in here
- the menu must by default unfault down to the subsection level, e.g. under "Installation & Configuration", subsections "QGIS Plugin", "Simulation Software" etc. is visible by default but for TELEMAC subsubsection users need to unfold "Simulation Software"
- limit section, subsection, subsubsections etc. contents to what is really needed; avoid AI slop and an overwhelming amount of information with technical slang
- write the documentation so that hydraulic engineers at a bachelor's degree do understand the wording
- users need to be able to define all requirements for plugin functionality through the graphical user interface (UI) of the aXqua QGIS plugin; if functionalities are missing, add them to the plugin
- the aXqua plugin has tabs and a "Help" menu that opens the documentation, shipped locally compiled with plugin installation, in a webbrower at exactly the docs section that is linked to the currently active tab
- in the aXqua plugin UI use tabs for each relevant configuration, that is, plugin must have:
    - a "Configuration" tab that holds all definitions explained in the subsections "Plugin Setup", "Postprocessors"
    - a "Case Setup" tab linked to "aXqua Case Setup" docs section
    - a "Preprocessing" tab linked to "Preprocessing" docs section
    - a "Hydraulic simulation" tab with sub-tabs for "Telemac" and "OpenFOAM", which link to "Hydraulic simulations" docs section and according subsections
    - a "Mesh convergence" tab that links to "Mesh convergence" docs section
    - a "Morphodynamic simulation" tab with sub-tabs for "Telemac" and "OpenFOAM", which link to "Morphodynamic simulations" docs section and according subsections
    - a "Calibration & validation" tab that links to "Calibration & validation" docs section
    - a "Postprocessing" tab with sub-tabs for QGIS, ParaView and visit-DAV (see below), which link to "Postprocessing" docs section and according subsections
    - a "Batch-processing" tab with visual batch-processing options and a "Detach" section that enables detaching the plugin session from the current QGIS session (so aXqua keeps running when one closes QGIS) and "Generate batch-processing script"; their help-click links to "Batch-processing" docs section and relevant subsections there
- the complex setup of config files for users, cases (projects), simulations (see "aXqua case setup" docs section) must be handled in pop-up windows (sub-apps?) where users can find "Save", "Cancel", and "Exit" buttons at the bottom-right with according save-functionality for `.axqua-profile` and `axqua-case` files that need to be updated according to user input and directly checked for correctness (files, factual, functional); correctness check must throw a warning if anything is not OK but should not hinder saving nor exiting the pop-up windows savely; 
- everytime and item concerned by an error or warning message should be tagged with an orange (warning) or dark-red bold (error) warning triangle in the plugin UI and clicking on the triangle will open the error message with direct link to that warning or error message in the docs section (see below)
- this computer for development purposes runs on Debian12 and has already all relevant software installed, including QGIS, Telemac, OpenFOAM, ParaView, and visit-DAV; you find the installation directories in the use CLAUDE.md file.
- we must use `OpenFOAM v2406`, and I placed an auto-installer script for this and seidDriftFoam and sediDriftFoam2 and an additionally needed stage-discharge relation outflow condition and postprocessing tools PrawView and visit-DAV here on this server into `/home/modelling/OpenFOAM/OpenFOAM-installer/` -- this should run with `python3 install.py --install-system-packages --examples --smoke-test` but that will require sudo rights
"""
---
* Purpose # meaning, perform standardized, state-of-the-art numerical simulations of georeferenced cases with open-source codes Telemac and OpenFOAM, including mesh convergence studies according to <cite-literature> and Bayesian calibration

* Installation & Configuration
	* QGIS plugin # say where one gets qgis from and how one can install this plugin through QGIS plugin manage (once it is published) -- if this requires the installation of dependencies, tell users how to get them; simulation software, however, is treated in the next section -- aXqua is probably OK to be executed by the QGIS-inherent Python installation
	* Simulation Software # aXqua QGIS plugin needs a wizard that runs users through the installation of Telemac and OpenFOAM on their systems, where users select their operating system (well, aXqua should be able to detect on what platform it is running)
		* TELEMAC # explain workflow through telemac installation wizard -- this may be based for linux on `/home/IWS/schwindt/hyhome-v2/get-started/install-telemac-autoinstaller.md`
		* OpenFOAM # this might be tricky -- mention that OpenFOAM users definitely also want to install Telemac because aXqua pulls a huge share of its computational efficiency from Telemac warm-up runs
	* Plugin setup
		* Plugin profile # users must be able to define a plugin profile (`.axq-profile` -- functionality to be created) that automatically configures paths Python, simulation software (see next subsubsection) and anything else that is necessary
		* Define solver bindings # explain how users can setup / configure the plugin -- these sections replace the current `telemac:` entry in the `case-config.yml` and add the same for OpenFOAM
		* <anything-needed-for-plugin-profile> # add this and if needed more subsubsections if users need to do other computer-specific definitions to make the plugin function but keep this reasonable and don't exaggerate on complexity of these sections
	* Postprocessors
        	* Visit # add -- I mean https://visit-dav.github.io/visit-website/
        	* ParaView # add

* Usage
	* aXqua case setup # explain what is an aXqua case and that a case config can be stored as `.axq-case` file -- that case file replaces the current `case-config.yml`
	     * Project paths # see `case-config.yml`, entry `project:` -- add some logical explanations for what users need to understand -- use must be able to just click through the plugin UI to define paths, they should have the option to but must not be required to manually type in directories
	     * Geodata # see `case-config.yml`, entry `geodata:` -- mention that for morphodynamics, aXqua can generate its own DEM of Differences (see below subsubsection)
	     * Boundaries # see `case-config.yml`, entry `boundaries:` -- add some logical explanations for what users need to understand -- place the `cases/gauge_data.py` script into the `scripts/` folder and explain how users can use it to autocomplete boundaries
	     * Initialization # see `case-config.yml`, entry `initialization:` -- add some logical explanations for what users need to understand 
	     * Mesh configuration # see `case-config.yml`, entry `mesh:` -- add some logical explanations for what users need to understand 
	     * Wall roughness # see `case-config.yml`, entry `friction:` -- and recall that the fluid experiences friction forces because of wall roughness
	     * Hydraulic simulation # see `case-config.yml`, entry `hydrodynamics:` -- add some logical explanations for what users need to understand 
	     * Morphodynamic simulation # eats `case-config.yml` entry `dem_of_difference:` (might be linked to Geodata) and probably needs more settings related to sediment grain sizes -- add some logical explanations for what users need to understand 
	* Preprocessing
        	* aXqua wokflow # explain how aXqua does preprocessing conceptually
        * Meshing # explain that meshes are only created once except for mesh convergence studies (see later sections)
        * Pre-processing checkup # explain how pre-processing completeness checks are performed
        * Telemac choices # explain specific preprocessing for telemac-only
        * OpenFOAM choices # explain specific preprocessing that are only relevant for subsequent OpenFOAM simulations
    * Hydraulic simulations
        * aXqua warm-up concept # explain the logic of telemac dry-runs, first in 2d, then 3d; users who want to run an OpenFOAM simulation should not disable a default (to be created option as tickbox `use Telemac warm-up`)
        * Telemac dry runs 2d/3d # explain how one can start the dry telemac2d/3d simulations
        * Telemac hotstarts # explain how to hot-start Telemac2d/3d simulations, especially unsteady ones
        * OpenFOAM # explain how to start an OpenFOAM simulation - in this tab is the optional checkbox to disable the default-enabled Telemac warm-up option
    * Mesh convergence
        * Mesh study setup # explain the purpose and what options one has here and also how a too-fine mesh might require switching the type of turbulence model -- add some logical explanations for what users need to understand
        * Run mesh convergences study # say that this can take a while and use considerable disk storage -- add some logical explanations for what users need to understand
        * Study report # explain the result of the mesh convergence study, what it tells, how the information should be used in the following considering the accuracy--computing-time tradeoff and that mesh independence is practically a myth
    * Morphodynamic simulations
        * Concept # explain sediment transport and active layer concepts; you may pick stuff from `/home/IWS/schwindt/hyhome-v2/numerics/telemac/gaia-<*>.md`, including graphics
        * Telemac-Gaia Setup # capacity to be added for `.cas` file generation?
        * OpenFOAM Setup # capacity to be added through Nils Reidar Olsen's toolboxs, that is pull in information from `https://www.pvv.ntnu.no/~nilsol/sediDriftFoam/`; I already downloaded the source code into `/home/IWS/schwindt/Downloads/sediDriftFoam/sourceCode/`; there also is a newer version of that code at  `https://pvv.ntnu.no/~nilsol/sediDriftFoam2/` which, I think uses some kind of volume-of-solid method to simulate topographic change and therefore is more computationally efficient
    * Calibration & validation
        * Introduction to Bayesian calibration # pull from my papers and `/home/IWS/schwindt/hydrobayescal/`
        * Ground truth data setup # adopt from `case-config.yml`, entry `ground_truth:`
        * Bayesian calibration & validation setup # adopt from `case-config.yml`, entry `calibration:` and what type of data split to use or different datasets to define for use for statistically independent calibration and validation
        * Run hydroBayesCal # explain how to run hydrobayescal through aXqua and also how to do a validation with data splitting
        * Quality analysis # explain key statistics that results from the calibration and validation as well as the prior and posterior distributions
    * Postprocessing
        * QGIS map generation # explain aXqua capacities to QGIS layers and print reports; there should also be predefined options to create streamline plots that use velocity magnitude and direction for streamlines/vectors and water depth and options to generate movies of unsteady simulations
        * ParaView # explain export from  QGIS aXqua plugin to ParaView formats and then how to use this with Paraview
        * Visit # explain export from  QGIS aXqua plugin to Visit formats and then how to use this with Visit
   * Batch-processing (visual automation) # add this functionality, which probably should create script to call with the descriptions in the next section)

* Automation & Terminal Usage
    * Terminal syntax # explain how Windows / Linux user can call and run aXqua and functionalities through Command Line / Terminal without opening QGIS, so in a completely non-graphical mode but make it clear that geodata inputs and models outputs should definitely be check beforehace
    * Batch-processing (headless)

* Code documentation
    * General structure & UML # generate an UML and explain the general structure
    * <adapt-source-code-structure> #  build the logics and headers of this section according to the UML / general structure

* Troubleshooting
    * General tips & logfiles # adop from the current help.md file and add that users can find logfiles and read warning and error messages to understand what is going on
    * Warning messages # explain code warning messages in log file and how they can be healed
    * Error messages # explain code error messages in log file and how they can be healed

* License and disclaimer # adapt as-is (only change if you see a necessity for changes)