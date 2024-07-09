import re
from datetime import timedelta

import geopandas as gpd
from pykml import parser
from shapely.geometry import Polygon


def parse_enmap_data(block):
    """
    Parse EnMAP TROPOMI collocated pair
    :param block: closest pairs text block
    :return: dictionary containing extracted file id and time difference in minutes
    """
    timediff_pattern = r"Time Difference: (.+)"
    filename_pattern = r"EnMAP File: Filename (.+?), Datetime:"
    filename_match = re.search(filename_pattern, block)
    timediff_match = re.search(timediff_pattern, block)
    # Extract name (datatake id + tile number) and temporal offset from text block
    if filename_match and timediff_match:
        filename = filename_match.group(1)
        time_parts = timediff_match.group(1).split(':')
        hours, minutes, seconds = map(float, time_parts)
        time_diff = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        time_diff_minutes = time_diff.total_seconds() / 60  # Convert offsets to minutes with decimals
        return {
            "filename": filename,
            "time_diff": time_diff_minutes
        }
    return None


def process_file(file):
    """
    Process closest pairs text file
    :param file: path to closest pairs text file
    :return: list of dictionaries containing extracted data
    """
    results = []
    with open(file, 'r') as file:
        data = file.read()
        blocks = data.split('--------------------')
        for block in blocks:
            case = parse_enmap_data(block)
            if case:
                results.append(case)
    return results


def extract_coordinates(placemark, namespace):
    """
    Extract coordinates from KML placemark
    :param placemark: KML placemark
    :param namespace: XML namespace
    :return: list of coordinates as (longitude, latitude) tuples
    """
    coordinates_text = placemark.find(f'{namespace}Polygon/{namespace}outerBoundaryIs/{namespace}LinearRing/{namespace}coordinates').text
    coordinates_list = []
    for coord in coordinates_text.strip().split():
        lon, lat, _ = map(float, coord.split(','))
        coordinates_list.append((lon, lat))
    return coordinates_list


def parse_enmap(file, time_differences):
    """
    Parse EnMAP KML file and match with time differences
    :param file: path to EnMAP KML file
    :param time_differences: list of dictionaries containing filename and time differences
    :return: list of dictionaries with filename (datatake id + tile number), time difference, and geometry
    """
    with open(file, 'r') as f:
        doc = parser.parse(f).getroot()
    namespace = '{http://www.opengis.net/kml/2.2}'
    cases = []
    # Check for matching filenames
    for placemark in doc.findall(f'.//{namespace}Placemark'):
        name_data = placemark.find(f'{namespace}name').text
        if name_data:
            filename = name_data
            matching_diff = next((td for td in time_differences if td['filename'] == filename), None)
            if matching_diff:
                coordinates = extract_coordinates(placemark, namespace)
                polygon = Polygon(coordinates)
                case = {
                    "filename": filename,
                    "time_diff": matching_diff['time_diff'],
                    "geometry": polygon
                }
                cases.append(case)
    return cases


def save_to_geopackage(cases, geopackage_path):
    """
    Save extracted cases as a GeoPackage
    :param cases: list of dictionaries with extracted data
    :param geopackage_path: path to the output GeoPackage file
    """
    gdf = gpd.GeoDataFrame(cases)
    gdf.to_file(geopackage_path, layer='tile_data', driver='GPKG')


if __name__ == "__main__":
    # Define timeframe
    target_year = 2024
    target_month = 4

    # Define paths
    enmap_kml = f'/home/adian/MA/overlap/enmap/KML_{target_year}_01_02_03_04_05_06_07_08_09_10_11_12-TILE.kml'
    text_output = f"collocated/{target_year}_{target_month}/closest_pairs_output_{target_year}_{target_month}.txt"
    geopackage_path = f"geopackages/output_{target_year}_{target_month}.gpkg"

    # Parse and export to gpkg
    time_differences = process_file(text_output)
    cases = parse_enmap(enmap_kml, time_differences)
    save_to_geopackage(cases, geopackage_path)
