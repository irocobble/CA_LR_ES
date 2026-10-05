@echo off
REM Builds dist\SAR_Detector.exe - a single file you can hand to someone who
REM has no Python installed.
REM
REM --add-data packs the model and the two importable folders INSIDE the exe.
REM Without it the exe starts and then fails at Connect, because features3.py
REM and devices.py are imported at runtime, not at build time.
cd /d "%~dp0"
pip install pyinstaller
pyinstaller --onefile --windowed --name SAR_Detector ^
  --add-data "ML;ML" ^
  --add-data "Recording_Via_IOT_Devices;Recording_Via_IOT_Devices" ^
  --hidden-import sklearn.utils._typedefs ^
  --hidden-import sklearn.utils._heap ^
  --hidden-import sklearn.utils._sorting ^
  --hidden-import sklearn.utils._vector_sentinel ^
  --hidden-import sklearn.neighbors._partition_nodes ^
  --hidden-import scipy.special._cdflib ^
  sar_gui.py
echo.
echo Built dist\SAR_Detector.exe
pause
