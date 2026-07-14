# Allsky Map Ping Client (systemd Service & Timer)

This directory contains prototype files for running a periodic "ping" client on camera machines. This client reports the camera's status and metadata back to the central Allsky Map API.

## Files

- [allsky-map-ping](file:///home/hamish/git/allsky-map/services/allsky-map-ping): The Python script that reads the configuration, formats the JSON payload, and calls the API. Uses Python 3 standard library only (no external dependencies required).
- [allsky-map-ping.conf](file:///home/hamish/git/allsky-map/services/allsky-map-ping.conf): Configuration template for the camera metadata, API keys, and endpoint URL.
- [allsky-map-ping.service](file:///home/hamish/git/allsky-map/services/allsky-map-ping.service): Systemd service definition that executes the python script.
- [allsky-map-ping.timer](file:///home/hamish/git/allsky-map/services/allsky-map-ping.timer): Systemd timer that triggers the service periodically (with random delay jitter and missed run catch-up).
- [install.sh](file:///home/hamish/git/allsky-map/services/install.sh): Interactive installer — handles everything below automatically.

---

## Installation Guide

### Quick Install (recommended)

Run the installer from the `services/` directory on the camera host. It will prompt you for all required values, write the config, install the systemd units, and fire a live test ping:

```bash
cd services/
sudo bash install.sh
```

To remove everything again:

```bash
sudo bash install.sh --uninstall
```

---

### Manual Installation

Follow these steps on the client camera machine to set up the periodic ping service:

### 1. Install the Script
Copy the python script to a binary directory and make it executable:
```bash
sudo cp allsky-map-ping /usr/local/bin/allsky-map-ping
sudo chmod +x /usr/local/bin/allsky-map-ping
```

### 2. Set Up Configuration
Create the configuration directory, copy the template, and edit it with your camera details and API key:
```bash
sudo mkdir -p /etc/allsky-map
sudo cp allsky-map-ping.conf /etc/allsky-map/ping.conf
sudo nano /etc/allsky-map/ping.conf
```

#### Securing the configuration file:
Since `/etc/allsky-map/ping.conf` contains your unique `API_KEY`, restrict its read permissions. Note that systemd reads the `EnvironmentFile` as root before dropping privileges, so this file remains readable by the service:
```bash
sudo chmod 600 /etc/allsky-map/ping.conf
```

### 3. Install Systemd Units
Copy the service and timer files to the systemd directory:
```bash
sudo cp allsky-map-ping.service /etc/systemd/system/
sudo cp allsky-map-ping.timer /etc/systemd/system/
```

### 4. Enable and Start the Timer
Reload the systemd daemon configuration, enable the timer to run on startup, and start it immediately:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now allsky-map-ping.timer
```

---

## Operations & Debugging

### Check Timer Status
Verify that the timer is active and see when it is scheduled to run next:
```bash
systemctl status allsky-map-ping.timer
systemctl list-timers --all | grep allsky-map-ping
```

### Test the Ping Manually
You can trigger the systemd service manually at any time to verify it pings the server successfully:
```bash
sudo systemctl start allsky-map-ping.service
```

### View Logs
Check the output of the ping execution using `journalctl`:
```bash
journalctl -u allsky-map-ping.service
```
Typical successful log output:
```text
Jul 09 14:30:00 raspberrypi systemd[1]: Starting Indi-Allsky Map Ping Client Service...
Jul 09 14:30:00 raspberrypi allsky-map-ping[12345]: Pinging Allsky Map API at: http://your-server/api/ping
Jul 09 14:30:01 raspberrypi allsky-map-ping[12345]: Success! Server response: {"message": "Success"}
Jul 09 14:30:01 raspberrypi systemd[1]: allsky-map-ping.service: Succeeded.
Jul 09 14:30:01 raspberrypi systemd[1]: Finished Indi-Allsky Map Ping Client Service.
```
