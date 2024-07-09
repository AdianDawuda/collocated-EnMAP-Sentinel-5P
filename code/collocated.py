import os
import re
from datetime import datetime, timedelta
from multiprocessing import Pool

import netCDF4 as nc
import numpy as np
import shapely.geometry
from pykml import parser
from scipy.spatial import KDTree

# Set multiprocessing params
ncore = "1"
os.environ["OMP_NUM_THREADS"] = ncore
os.environ["OPENBLAS_NUM_THREADS"] = ncore
os.environ["MKL_NUM_THREADS"] = ncore
os.environ["VECLIB_MAXIMUM_THREADS"] = ncore
os.environ["NUMEXPR_NUM_THREADS"] = ncore


def parse_enmap(file):
    """
    Extract name, date+time, and spatial extent of EnMAP acquisitions from KML metadata
    :param file: KML file (non-operational product)
    :return: list of dictionary objects (one for each tile)
    """
    # Load KML
    with open(file, "r") as f:
        doc = parser.parse(f)
    # Get KML root
    root = doc.getroot()
    namespace = "{http://www.opengis.net/kml/2.2}"
    enmap_files = []
    # Extract all name, date+time, and extent
    for pm in root.iter(namespace + "Placemark"):
        name = pm.find(namespace + "name").text
        clouds = pm.find(namespace + "ExtendedData") \
            .find(namespace + "Data[@name='clouds']") \
            .find(namespace + "value").text
        date = pm.find(namespace + "ExtendedData") \
            .find(namespace + "Data[@name='date']") \
            .find(namespace + "value").text
        time = pm.find(namespace + "ExtendedData") \
            .find(namespace + "Data[@name='time']") \
            .find(namespace + "value").text
        # Combine date and time
        dt_format = "%Y-%m-%d %H:%M:%S.%f"
        time_str = time[:time.index(".") + 4]
        dt_string = f"{date} {time_str}"
        time_dt = datetime.strptime(dt_string, dt_format)
        # Filter year, month, and day
        if (target_year is None or time_dt.year == target_year) and \
                (target_month is None or time_dt.month == target_month) and \
                (target_day is None or time_dt.day == target_day):
            filtered_time = time_dt
        else:
            continue
        # Get spatial footprint
        polygon = pm.find(namespace + "Polygon")
        coordinates_str = polygon.find(namespace + "outerBoundaryIs") \
            .find(namespace + "LinearRing") \
            .find(namespace + "coordinates").text
        # Split and clean the coordinates string
        extent = []
        for point_str in coordinates_str.strip().split(" "):
            lon, lat, alt = point_str.split(",")
            extent.append((float(lon), float(lat)))
        # Add dictionary objects to list
        file_data = {
            "filename": name,
            "extent": extent,
            "center_time": filtered_time,
            "clouds": clouds
        }
        enmap_files.append(file_data)
    return enmap_files


def get_tropomi_extent(file):
    """
    Extract image extent from TROPOMI NetCDF file
    :param file: TROPOMI NetCDF file
    :return: image extent
    """
    # Open NetCDF file
    dataset = nc.Dataset(file, "r")
    # Extract spatial footprint
    coords_str = dataset["METADATA/EOP_METADATA/om:featureOfInterest/eop:multiExtentOf/gml:surfaceMembers/gml:exterior"]
    coords_str = coords_str.__dict__
    coords_str = coords_str["gml:posList"]
    coords = re.findall(r"(-?\d+\.\d+)\s(-?\d+\.\d+)", coords_str)
    # Create list of coordinate tuples with lon lat order
    coords_list = [(float(coord[1]), float(coord[0])) for coord in coords]
    dataset.close()
    return coords_list


def get_tropomi_filename_date(file):
    """
    Extract day as a datetime object from the TROPOMI filename
    :param file: TROPOMI filename
    :return: datetime object representing the day
    """
    file = os.path.basename(file)
    # Extract date portion from the filename
    date_str = file[20:28]  # (yyyymmdd)
    # Convert date string to datetime object
    date_obj = datetime.strptime(date_str, "%Y%m%d")
    return date_obj


def get_tropomi_datetime(file, overlap):
    """
    Extract central acquisition time of overlapping part of TROPOMI NetCDF file
    :param overlap: area of overlap with EnMAP acquisition
    :param file: TROPOMI NetCDF file
    :return: central acquisition time as datetime object
    """
    tropomi_scanlines = []
    # Get central time (ms)
    for coordinates in overlap:
        tropomi_scanlines.append(get_tropomi_scanline(file, coordinates))
    min_tropomi_scanline = min(tropomi_scanlines)
    max_tropomi_scanline = max(tropomi_scanlines)
    min_tropomi_time = get_tropomi_scanline_time(file, min_tropomi_scanline)
    max_tropomi_time = get_tropomi_scanline_time(file, max_tropomi_scanline)
    center_tropomi_time_ms = round((min_tropomi_time + max_tropomi_time) / 2)
    center_tropomi_time = timedelta(milliseconds=center_tropomi_time_ms)
    # Transform central time to UTC
    dataset = nc.Dataset(file, "r")
    file_time = dataset["/PRODUCT/time"]
    time_delta = timedelta(seconds=int(file_time[0]))
    reference_time = datetime(2010, 1, 1, 0, 0, 0)
    datetime_date = reference_time + time_delta + center_tropomi_time
    dataset.close()
    return datetime_date


def get_tropomi_scanline_time(file, scanline):
    """
    Get TROPOMI delta time value at a specific scanline
    :param file: TROPOMI NetCDF
    :param scanline: scanline index
    :return: time at scanline (in ms)
    """
    dataset = nc.Dataset(file, "r")
    delta_time_var = dataset["/PRODUCT/delta_time"]
    # Extract delta time scanline
    delta_time_at_scanline = delta_time_var[0, scanline]
    dataset.close()
    return delta_time_at_scanline


def get_tropomi_scanline(file, coordinate):
    """
    Extract closest TROPOMI scanline (and ground pixel) at a specific coordinate
    :param file: TROPOMI NetCDF
    :param coordinate: EPSG:4326 coordinate
    :return: closest scanline
    """
    target_latitude = coordinate[1]
    target_longitude = coordinate[0]
    # Load NetCDF
    dataset = nc.Dataset(file, "r")
    latitude = dataset["/PRODUCT/latitude"][0]
    longitude = dataset["/PRODUCT/longitude"][0]
    # Build KDTree
    coords = np.dstack([latitude.ravel(), longitude.ravel()])[0]
    tree = KDTree(coords)
    # Find nearest ground pixel
    distance, index = tree.query([target_latitude, target_longitude])
    # Extract scanline and ground pixel
    scanline, ground_pixel = np.unravel_index(index, latitude.shape)
    dataset.close()
    return scanline


def check_intersect(extent_1, extent_2):
    """
    Check if two polygons intersect (used to check intersection with AOI and between EnMAP and TROPOMI acquisitions)
    :param extent_1: spatial extent of first polygon
    :param extent_2: spatial extent of second polygon
    :return: True (polygons intersect) or False (polygons don't intersect)
    """
    polygon_1 = shapely.Polygon(extent_1)
    polygon_2 = shapely.Polygon(extent_2)
    return polygon_1.intersects(polygon_2)


def get_intersect(extent_1, extent_2):
    """
    Calculate the intersection of two polygons and return the coordinates of the intersection polygon
    :param extent_1: spatial extent of first polygon
    :param extent_2: spatial extent of second polygon
    :return: coordinates of the intersection polygon
    """
    polygon_1 = shapely.Polygon(extent_1)
    polygon_2 = shapely.Polygon(extent_2)
    intersection_polygon = polygon_2.intersection(polygon_1)
    intersection_coords = intersection_polygon.exterior.coords
    return intersection_coords


def get_candidates_enmap(metadata_file_list):
    """
    Loop through acquisitions (KML metadata files) and add acquisitions intersecting with AOI to a list
    :param metadata_file_list: list of acquisition metadata files
    :return: list of AOI intersecting acquisitions as dictionaries
    """
    candidate_files = []
    # Loop through acquisitions
    for file in metadata_file_list:
        image_extent = file["extent"]
        # Check if acquisition intersects with AOI and add to list if it does
        if check_intersect(image_extent, area_of_interest):
            candidate_files.append(file)
    return candidate_files


def get_candidates_tropomi(file_list):
    """
    Loop through TROPOMI acquisitions and add acquisitions intersecting with AOI to a list
    :param file_list: list of acquisitions
    :return: list of AOI intersecting acquisitions as dictionaries
    """
    candidate_files = []
    # Loop through acquisitions
    for file in file_list:
        image_extent = get_tropomi_extent(file)
        # Check if acquisition intersects with AOI and add to list if it does
        if check_intersect(image_extent, area_of_interest):
            filename_date = get_tropomi_filename_date(file)
            file_data = {
                "filename": file,
                "extent": image_extent,
                "filename_date": filename_date
            }
            candidate_files.append(file_data)
    return candidate_files


def check_dates(date1, date2):
    """
    Check if two dates are the same (same year, month, and day)
    :param date1: First date
    :param date2: Second date
    :return: True if the dates are the same, False otherwise
    """
    return date1.year == date2.year and date1.month == date2.month and date1.day == date2.day


def process_enmap_file(enmap_file, tropomi_candidate_files):
    """
    Iterate over TROPOMI candidate files and return EnMAP file with closest TROPOMI
    :param enmap_file: EnMAP candidate file
    :param tropomi_candidate_files: List of TROPOMI candidate files
    :return: EnMAP file with closest TROPOMI
    """
    closest_tropomi = None
    min_time_diff = timedelta.max
    overlap = None
    for tropomi_file in tropomi_candidate_files:
        if check_dates(enmap_file["center_time"], tropomi_file["filename_date"]):
            if check_intersect(enmap_file["extent"], tropomi_file["extent"]):
                try:
                    overlap = get_intersect(enmap_file["extent"], tropomi_file["extent"])
                except shapely.errors.GEOSException as e:
                    print(f"Intersection Error: {e}")
                    continue
                try:
                    tropomi_file["center_time"] = get_tropomi_datetime(tropomi_file["filename"], overlap)
                    time_diff = abs(enmap_file["center_time"] - tropomi_file["center_time"])
                except ValueError as e:
                    print(f"Value Error: {e}")
                    continue
                if time_diff < min_time_diff:
                    min_time_diff = time_diff
                    closest_tropomi = tropomi_file
                    print(f"{enmap_file["filename"]} offset: {time_diff}")
    return {
        "overlap": overlap,
        "enmap": enmap_file,
        "tropomi": closest_tropomi,
        "time_difference": min_time_diff
    } if closest_tropomi else None


def get_closest_pairs(enmap_candidate_files, tropomi_candidate_files):
    """
    Get closest pairs of TROPOMI and EnMAP acquisitions using multiprocessing
    :param enmap_candidate_files: EnMAP candidate files
    :param tropomi_candidate_files: TROPOMI candidate files
    :return: list of closest pairs (dictionaries)
    """
    # Create process pool
    with Pool() as pool:
        results = pool.starmap(process_enmap_file,
                               [(enmap_file, tropomi_candidate_files) for enmap_file in enmap_candidate_files])
    # Filter valid results
    return [result for result in results if result is not None]


def export_pairs(pairs):
    """
    Export text file of closest collocated cases
    :param pairs: list of dictionaries containing overlapping acquisitions (dictionary objects) and the time offset
    :output: text file
    """
    # Set output filename
    output_filename = f"closest_pairs_output_{target_year}"
    if target_month:
        output_filename += f"_{target_month}"
    if target_day:
        output_filename += f"_{target_day}"
    output_filename += ".txt"
    # Export collocated cases
    with open(output_filename, "w") as output_file:
        for pair in pairs:
            overlap_coords = list(pair["overlap"])
            output_file.write(f"Overlap: {overlap_coords}\n")
            output_file.write(f"EnMAP File: Filename {pair["enmap"]["filename"]}, Datetime: {pair["enmap"]["center_time"]}\n")
            output_file.write(f"TROPOMI File: Filename {os.path.basename(pair["tropomi"]["filename"]).split(".")[0]}, Datetime: {pair["tropomi"]["center_time"]}\n")
            output_file.write(f"Cloud Fraction (EnMAP): {pair["enmap"]["clouds"]}\n")
            output_file.write(f"Time Difference: {pair["time_difference"]}\n")
            output_file.write("--------------------\n")

    print(f"collocated cases exported as {output_filename}")


if __name__ == "__main__":
    # Set AOI
    area_of_interest = [(-27, 72), (-27, 34),
                        (43, 34), (43, 72)]  # Europe (top left, bottom left, bottom right, top right)

    # Define time of interest
    target_year = 2024
    target_month = 2
    target_day = None

    # Define EnMAP metadata KML file
    enmap_kml = "/home/adian/MA/overlap/enmap/KML_2024_01_02_03_04_05_06_07_08_09_10_11_12-TILE.kml"

    # Define directory containing the TROPOMI files
    tropomi_dir = "/home/adian/MA/overlap/tropomi/"

    tropomi_files = [tropomi_dir + filename for filename in os.listdir(tropomi_dir)]

    # Lists containing dictionary objects of acquisitions intersecting with the AOI
    enmap_metadata_files = parse_enmap(enmap_kml)
    enmap_candidate_files = get_candidates_enmap(enmap_metadata_files)

    tropomi_candidate_files = get_candidates_tropomi(tropomi_files)

    print(f"EnMAP candidates: {len(enmap_candidate_files)}")
    print(f"TROPOMI candidates: {len(tropomi_candidate_files)}")

    # Get temporally closest acquisitions
    closest_pairs = get_closest_pairs(enmap_candidate_files, tropomi_candidate_files)

    # Print closest pairs
    print("Closest acquisitions:")
    for pair in closest_pairs:
        print(f"EnMAP File: {pair["enmap"]["filename"]}")
        print(f"TROPOMI File: {pair["tropomi"]["filename"]}")
        print(f"Time Difference: {pair["time_difference"]} \n")
        #print(f"overlap: {pair["overlap"]} \n")

    # Export closest pairs
    export_pairs(closest_pairs)



