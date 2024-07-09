from qgis.core import QgsProject, QgsPrintLayout
import processing

# Define timeframe
target_year = 2023
target_month = 5

# Define file paths
input_gpkg = f"/home/adian/AppDev/geopackages/output_{target_year}_{target_month}.gpkg"
output_img = f"/home/adian/AppDev/plots/colocated_time_differences_{target_year}_{target_month}.png"
collocated_style = "/home/adian/AppDev/collocated_style_gpkg.qml"
world_map = "/usr/share/qgis/resources/data/world_map.gpkg|layername=countries"
print_layout_file = "/home/adian/AppDev/enmap_print_layout.qpt"

# Add print layout to project
project = QgsProject.instance()
layout = QgsPrintLayout(project)
layout.initializeDefaults()
with open(print_layout_file) as f:
    template_content = f.read()
    doc = QDomDocument()
    doc.setContent(template_content)
    items, ok = layout.loadFromTemplate(doc, QgsReadWriteContext(), True)
    layout.setName('collocated_print_layout')
    project.layoutManager().addLayout(layout)

# Add basemap and gpkg to map
basemap = iface.addVectorLayer(world_map, "World map", "ogr")
gpkg_layer = iface.addVectorLayer(input_gpkg, "Closest TROPOMI\nacquisition (minutes)", "ogr")

# Set style for gpkg
alg_params = {
    'INPUT': gpkg_layer,
    'STYLE': collocated_style
}
processing.run('qgis:setstyleforvectorlayer', alg_params)

# Export print layout as image
alg_params = {
    'ANTIALIAS': True,
    'DPI': 300,
    'GEOREFERENCE': False,
    'INCLUDE_METADATA': False,
    'LAYERS': None,
    'LAYOUT': 'collocated_print_layout',
    'OUTPUT': output_img
}
processing.run('native:printlayouttoimage', alg_params)

# Remove all layers from the project
project.removeAllMapLayers()

