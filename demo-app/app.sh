echo "Starting demo app..."

if [ ! -f config.txt ]; then
    echo "Error: config.txt not found"
    exit 1
fi

echo "Loaded config: $(cat config.txt)"
echo "App is running"
sleep 3600
