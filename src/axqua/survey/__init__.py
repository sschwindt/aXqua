"""Survey planning: where to measure so that a calibration has the data it needs.

A calibration can only say something about a parameter where measurements respond to it. This package turns that around: from the roughness zones of the case, the calibration parameters it declares (and any the user adds) and the flow field of a steady run at the discharge expected on the survey day, it proposes where to measure, in which order, and how long it takes. Four kinds of survey are planned, alone or together:

* **ground-based** point velocimetry by wading (an acoustic Doppler velocimeter such as the FlowTracker), with the walk of every surveyor (:mod:`axqua.survey.ground`);
* **airborne** surface velocimetry from drone video (PTV or PIV, evaluated with pXv), with the hover stations of the drone (:mod:`axqua.survey.drone`);
* **kayak** drifter velocimetry after Barker et al. (2018), with the drift lines between a put-in and a take-out (:mod:`axqua.survey.kayak`);
* **water temperature** with fixed loggers or a mobile probe, by patches of slow, shallow and of fast, deep water (:mod:`axqua.survey.temperature`).

:func:`axqua.survey.planner.make_plan` writes ``survey-plan.gpkg`` (the layers QGIS shows), one map and one single-page factsheet per kind of survey, and ``survey-plan.json`` with every number. The QGIS plugin reaches all of it through ``axqua survey`` (:mod:`axqua.surveycli`).
"""

from axqua.survey.settings import SurveySettings

__all__ = ["SurveySettings"]
