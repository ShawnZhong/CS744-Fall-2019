./clean.sh
dstat --cpu --mem --net --output report.csv & python3 main.py 1 0