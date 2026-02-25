#!/bin/bash

cd data/TNDS

tfl_old=$(ls -l L.zip)
wget -qN https://tfl.gov.uk/tfl/syndication/feeds/journey-planner-timetables.zip -O L.zip
tfl_new=$(ls -l L.zip)

cd ../..

if [[ $tfl_old != $tfl_new ]]; then
    echo 'L.zip'
    ./manage.py import_transxchange data/TNDS/L.zip
fi