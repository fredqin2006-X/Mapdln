# Third-party components and data

Mapdln application code retains the original MIT license in LICENSE.

- PySide6 / Shiboken6 / Qt: https://www.qt.io/licensing/ ; upstream source https://code.qt.io/ and https://download.qt.io/official_releases/QtForPython/ . Distributed dynamically as DLLs within the executable's extracted runtime. Applicable component licenses, including LGPL/GPL alternatives and Chromium third-party notices, belong to their upstream components; the executable may be unpacked and its dynamic libraries replaced using PyInstaller extraction tooling.
- Qt WebEngine uses Chromium: https://doc.qt.io/qt-6/qtwebengine-licensing.html
- Leaflet 1.9.4: BSD-2-Clause, bundled license `mapdln/web/LEAFLET-LICENSE.txt`.
- Rasterio / GDAL, PROJ / pyproj, Shapely / GEOS: https://github.com/rasterio/rasterio , https://gdal.org/ , https://proj.org/ , https://github.com/pyproj4/pyproj , https://github.com/shapely/shapely . Licenses are provided by each upstream project and wheel metadata.
- NumPy, SciPy, Pillow, Requests, mercantile, PySocks, PyInstaller: see installed dependency metadata and upstream distributions for their respective license texts. PyInstaller bootloader redistribution follows its license exception.

Online map services are not redistributed as application source. Mapbox imagery remains © Mapbox / Maxar / respective providers; OpenStreetMap attribution is displayed where used. AMap (高德) and Baidu (百度) maps retain SDK attribution and require suitable developer API authorization.

FABDEM V1-2 is approximately 30 m elevation data from University of Bristol under CC BY-NC-SA 4.0: https://research-information.bris.ac.uk/en/datasets/fabdem-v1-2/ . Usage of downloaded data follows its provider terms independently of the application's MIT license. No user credentials or licensed map imagery are bundled in the application download.
