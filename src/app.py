import io
import zipfile
import streamlit as st
import pandas as pd
import numpy as np
from geofeather import from_geofeather, to_geofeather
import geopandas as gpd 
from shapely.geometry import Point
import folium 
from streamlit_folium import st_folium
import requests
import csv 
import geofeather
import os 

# Get and save data from the gas stations and the daily oil price 
def get_data_and_save(link: str, path: str) -> None:
    
    response = requests.get(link)
    
    with open(path, 'w', encoding='utf-8', newline='') as f:
        
        f.write(response.text)
        f.close()
        

# Create GeoJSON file
def create_feather(df, save_path):
    df_geo = df.copy()      
    df_geo['geometry'] = gpd.points_from_xy(df_geo.Longitudine, df_geo.Latitudine)
    df_geo = gpd.GeoDataFrame(df_geo, crs='EPSG:4326')
    geofeather.to_geofeather(df_geo, save_path)
    
        
        
def download_data():
    link_price = 'https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv'
    path_price = 'data/raw/price_at_8am.csv'

    link_gas_station = 'https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv'
    path_gas_station = 'data/raw/data_gas_station.csv'

    link_geospatial_reference_istat = 'https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv'
    path_geospatial_reference_istat = 'data/raw/geospatial_reference.csv'

    get_data_and_save(link_price, path_price)
    get_data_and_save(link_gas_station, path_gas_station)
    get_data_and_save(link_geospatial_reference_istat, path_geospatial_reference_istat)

    with open('data/raw/data_gas_station.csv', 'r', encoding='utf-8') as f:
        text = csv.reader(f, delimiter=';')
        next(text)
        
        modified_rows = []
        
        for row in text:
            if len(row) == 11: 
                row[-5] = row[-4]
                row[-4] = row[-3]
                row[-3] = row[-2]
                row[-2] = row[-1]
                row.pop()
                modified_rows.append(row)
                print(f'Modify this row: {row}')
            else:
                modified_rows.append(row)

        # Save the new file 
        with open('data/interim/data_gas_station.csv', 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            
            writer.writerows(modified_rows)

        f.close()
        
    # Create GeoJson file 
    gas_station = pd.read_csv('data/interim/data_gas_station.csv')
        
    price = (
        pd.read_csv
        ('data/raw/price_at_8am.csv', 
        delimiter=';', 
        skiprows=1, 
        parse_dates=['dtComu'])
    )

    merge_df = price.merge(gas_station, on='idImpianto')
    create_feather(merge_df, 'data/processed/final_gas_station.feather')


    # Extract administrative boundaries (shp files)
    if not os.path.exists('data/processed/final_comuni.feather'):
        link = 'https://www.istat.it/storage/cartografia/confini_amministrativi/generalizzati/2023/Limiti01012023_g.zip'
        response = requests.get(link)

        with zipfile.ZipFile(io.BytesIO(response.content), 'r') as zip_file:
            zip_file.extractall('data/raw/comuni')
            
        comuni = gpd.read_file(r'data\raw\comuni\Limiti01012023_g\Com01012023_g\Com01012023_g_WGS84.shp')
        to_geofeather(comuni, 'data/processed/final_comuni.feather')

    # Save the description of istat's geospatial description.
    with open('data/processed/geospatial_reference.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
    


@st.cache_data
def load_data():
    
    download_data()
    
    geodf = from_geofeather('data/processed/final_gas_station.feather')
    comuni = from_geofeather('data/processed/final_comuni.feather')
    
    mapping = {
    'Ã¹': 'ù',
    'Ã²': 'ò',
    'Ã¨': 'è',
    'Ã©': 'é',
    'Ã ': 'à',
    'Ã¬': 'ì'
}

    comuni.COMUNE = comuni.COMUNE.replace(mapping, regex=True)
    
    return geodf, comuni 


# Find the polygon and the centroid 
def closest_stations(name_municipality, self_service, type_fuel):
    comune = comuni.loc[comuni.COMUNE.str.startswith(name_municipality)].copy()
    center = comune.geometry.centroid
    center = center.to_crs('EPSG:4326')

    target_lon_circle, target_lat_circle = center.geometry.x.item(), center.geometry.y.item()
    target_point_circle = gpd.GeoDataFrame(geometry=[Point(target_lon_circle, target_lat_circle)], crs='EPSG:4326')
    target_projected = target_point_circle.to_crs('EPSG:3857') 

    radius_meters = 15000  # Radius in meters
    buffer_projected = target_projected.buffer(radius_meters)
    buffer = buffer_projected.to_crs('EPSG:4326')

    if self_service == 'Yes':
        self_service = 1
    else:
        self_service = 0 

    stat_circle = (geodf.loc[(geodf.descCarburante == type_fuel) & (geodf.isSelf == self_service)].
            within(buffer.geometry.item()))
    indici = stat_circle.index[stat_circle]
    closest_station = geodf.loc[indici].sort_values(by='prezzo').copy()
    
    return closest_station, center, comune 

# Create the folium map 
def create_folium_map(stations_close_municipality, center, comune):
    center_latitude = center.geometry.centroid.y.mean()
    center_longitude = center.geometry.centroid.x.mean()
    location = [center_latitude, center_longitude]

    map_price = folium.Map(
        location=location,
        zoom_start=12
        )

    colormap = folium.LinearColormap(colors=['green', 'red'],
                                    vmin=stations_close_municipality.prezzo.min(),
                                    vmax=stations_close_municipality.prezzo.max()
                                    )
    
    colormap.caption = 'The range of fuel prices'    

    comune_layer_geojson = folium.GeoJson(comune, name='geojson', tooltip=comune['COMUNE'].item()) 

    fg = folium.FeatureGroup(name='markers')
    fg.add_child(comune_layer_geojson)
    
    for _, row in stations_close_municipality.iterrows():
        
        data_dict = {
            'Price': [row['prezzo']],
            'Address': [' '.join(row['Indirizzo'].lower().capitalize().split()[:-1])],
            'Comune': [row['Comune'].lower().capitalize()], 
            'Cap': [row['Indirizzo'].split()[-1]]
        }
        popup_df = pd.DataFrame(data_dict)
        popup = popup_df.to_html(index=False, classes='table table-striped table-hover')
        
        fg.add_child(
            folium.CircleMarker([row['Latitudine'], 
                            row['Longitudine']],
                            radius=9,
                            color=colormap(row['prezzo']),
                            fill=True, 
                            fill_opacity=0.7,
                            popup=popup,
                            tooltip=row['Bandiera'])
        )
        

    # fg.add_child(colormap)
    colormap.add_to(map_price)
    return map_price, fg, location



if __name__ == '__main__':
    
    st.title("It's time to save money :sunglasses:")
    st.divider()
    st.subheader("This project aims to find the best petrol station in a location of your choice:fuelpump:",
                help="It works only in Italy")
    st.divider()
    

    
    data_load_state = st.text('Loading data...')
    geodf, comuni = load_data()
    data_load_state.text('Loading data...done!')

    list_comuni = comuni.COMUNE.sort_values()
    list_carburante = geodf.descCarburante.unique()

    col1, col2, col3 = st.columns(3)

    with col1:
        name_municipality = st.selectbox('Please, select a municipality', list_comuni)

    with col2:
        self_service = st.radio('Self Service?', options=['Yes', 'No'])
        
    with col3:
        type_fuel = st.selectbox('Select the type of fuel', list_carburante)
    
    
    stations_close_municipality, center, comune = closest_stations(name_municipality, self_service, type_fuel)

    st.write(f'These are the cheapest oil stations closest to {name_municipality}')
    
    stations_close_municipality.dtComu = pd.to_datetime(stations_close_municipality['dtComu']).dt.date
    
    st.dataframe(
        stations_close_municipality.sort_values(by='prezzo')[['prezzo', 'Bandiera', 'Comune', 'Indirizzo', 'dtComu']],
        column_config={
            'prezzo' :  'Price',
            'Bandiera' : 'Company',
            'Comune' : 'Municipality',
            'Indirizzo' : 'Address',
            'dtComu' : 'Date'
        }, 
        hide_index=True)
    
    
    

    # Create the folium map 
    # map_price = create_folium_map(stations_close_municipality, center)
    # st_data = st_folium(map_price, width = 725)
    map_price, fg, location = create_folium_map(stations_close_municipality, center, comune)
    st_data = st_folium(map_price, feature_group_to_add=fg, center=location, width = 650, height=500, zoom=12)
    
    st.caption('Racoons :raccoon::raccoon:')