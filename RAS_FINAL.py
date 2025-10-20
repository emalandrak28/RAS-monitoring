#!/usr/bin/env python3
import board
import busio
import time
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import datetime
import http.client
import urllib
import RPi.GPIO as GPIO
from ds18b20 import DS18B20
import json
import shutil
from threading import Timer
import logging
import statistics
import ssl
import requests
import subprocess
import smbus2
import yaml
import os
from dotenv import load_dotenv

# Load environment variables from RAS.env
load_dotenv('RAS.env')

# Load configuration file
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config['paths']['log_file']),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration from files
THINGSBOARD_URL = config['thingsboard']['url']
PORT = config['thingsboard']['port']
PUSHOVER_TOKEN = os.getenv('PUSHOVER_TOKEN')
PUSHOVER_USER = os.getenv('PUSHOVER_USER')
PUBLISH_INTERVAL = config['sensors']['publish_interval']
SAMPLE_COUNT = config['sensors']['sample_count']
DISCARD_COUNT = config['sensors']['discard_count']

# UPS Configuration from config
UPS_CHECK_INTERVAL = config['ups']['check_interval']
UPS_SHUTDOWN_THRESHOLD = config['ups']['shutdown_threshold']
UPS_I2C_ADDR = config['ups']['i2c_address']
UPS_BATTERY_REG = 0x02

# Device credentials from passwords.env
DEVICE_CREDENTIALS = {
    "temperature": os.getenv('TB_TEMPERATURE_TOKEN'),
    "pH": os.getenv('TB_PH_TOKEN'),
    "conductivity": os.getenv('TB_CONDUCTIVITY_TOKEN'),
    "level": os.getenv('TB_LEVEL_TOKEN'),
    "recirc_pump": os.getenv('TB_RECIRC_PUMP_TOKEN'),
    "dispense_pump": os.getenv('TB_DISPENSE_PUMP_TOKEN'),
    "ups_battery": os.getenv('TB_UPS_BATTERY_TOKEN')
}

# Thresholds from config
ALERT_THRESHOLDS = config['alert_thresholds']

class UPSMonitor:
    def __init__(self, shutdown_threshold=20, check_interval=60):
        self.shutdown_threshold = shutdown_threshold
        self.check_interval = check_interval
        self.shutdown_triggered = False
        self.last_battery_level = 100
        
    def get_battery_level(self):
        """
        Read battery level from Waveshare UPS HAT
        Returns battery percentage (0-100) or None if error
        """
        try:
            # Method 1: Read from I2C (primary method for Waveshare UPS)
            bus = smbus2.SMBus(config['hardware']['i2c_bus'])
            
            # Read 2 bytes from battery level register
            data = bus.read_word_data(UPS_I2C_ADDR, UPS_BATTERY_REG)
            
            # Convert to percentage (adjust based on your specific Waveshare UPS)
            battery_level = data & 0xFF
            
            # Ensure value is within valid range
            battery_level = max(0, min(100, battery_level))
            
            bus.close()
            return battery_level
            
        except Exception as e:
            logger.error(f"Error reading UPS battery via I2C: {e}")
            
            # Method 2: Try sysfs interface (alternative method)
            try:
                battery_paths = [
                    "/sys/class/power_supply/ups-battery/capacity",
                    "/sys/class/power_supply/battery/capacity",
                    "/sys/class/power_supply/ups/capacity",
                ]
                
                for path in battery_paths:
                    if os.path.exists(path):
                        with open(path, 'r') as f:
                            level = int(f.read().strip())
                            logger.info(f"Read UPS battery from sysfs: {level}%")
                            return level
            except Exception as e2:
                logger.error(f"Error reading UPS battery from sysfs: {e2}")
                
        return None
        
    def safe_shutdown(self):
        """Perform safe shutdown with notification"""
        if self.shutdown_triggered:
            return
            
        self.shutdown_triggered = True
        logger.warning(f"UPS Battery critical ({self.last_battery_level}%). Initiating safe shutdown.")
        
        # Send emergency notification
        try:
            alert_message = f"EMERGENCY: RAS System shutting down due to low UPS battery ({self.last_battery_level}%). System will power off in 30 seconds!"
            self.send_emergency_notification(alert_message)
        except Exception as e:
            logger.error(f"Failed to send emergency notification: {e}")
            
        # Wait a bit for notification to be sent and any cleanup
        time.sleep(30)
        
        # Perform shutdown
        try:
            logger.info("Initiating system shutdown command")
            subprocess.run(['sudo', 'shutdown', '-h', 'now'], check=True)
        except Exception as e:
            logger.error(f"Shutdown command failed: {e}")
            # Fallback: try direct poweroff
            try:
                subprocess.run(['sudo', 'poweroff'], check=True)
            except Exception as e2:
                logger.error(f"Poweroff also failed: {e2}")
                
    def send_emergency_notification(self, message):
        """Send emergency shutdown notification via Pushover"""
        try:
            context = ssl.create_default_context()
            conn = http.client.HTTPSConnection("api.pushover.net:443", context=context)
            conn.request("POST", "/1/messages.json",
                urllib.parse.urlencode({
                    "token": PUSHOVER_TOKEN,
                    "user": PUSHOVER_USER,
                    "message": message,
                    "priority": 2,  # Emergency priority
                    "retry": 30,    # Retry every 30 seconds
                    "expire": 300   # Stop retrying after 5 minutes
                }), {"Content-type": "application/x-www-form-urlencoded"})
            response = conn.getresponse()
            if response.status != 200:
                logger.error(f"Emergency notification failed: {response.status} {response.reason}")
            else:
                logger.info("Emergency notification sent successfully")
            conn.close()
        except Exception as e:
            logger.error(f"Failed to send emergency notification: {str(e)}")
            
    def check_battery(self):
        """Check battery level and trigger shutdown if needed"""
        battery_level = self.get_battery_level()
        self.last_battery_level = battery_level
        
        if battery_level is not None:
            logger.info(f"UPS Battery level: {battery_level}%")
            
            # Check for shutdown condition
            if battery_level <= self.shutdown_threshold:
                logger.warning(f"UPS Battery level {battery_level}% <= threshold {self.shutdown_threshold}%")
                self.safe_shutdown()
                return False  # Stop further monitoring
                
            # Check for low battery warning (but not shutdown yet)
            elif battery_level <= ALERT_THRESHOLDS["ups_battery_low"]:
                warning_message = f"UPS Battery low: {battery_level}%. System will shutdown at {self.shutdown_threshold}%."
                logger.warning(warning_message)
                try:
                    self.send_emergency_notification(warning_message)
                except Exception as e:
                    logger.error(f"Failed to send low battery warning: {e}")
                    
        else:
            logger.warning("Could not read UPS battery level")
            
        return True  # Continue monitoring

class ThingsBoardHTTPClient:
    def __init__(self):
        self.base_url = THINGSBOARD_URL
        self.device_tokens = DEVICE_CREDENTIALS
        self.session = requests.Session()
        
        # Configure SSL context for secure connections
        self.session.verify = True  # Verify SSL certificates
        logger.info("ThingsBoard HTTP client initialized with SSL")

    def send_telemetry(self, device_name, data):
        """Send telemetry data via HTTPS API"""
        try:
            token = self.device_tokens.get(device_name)
            if not token:
                logger.error(f"No token found for device: {device_name}")
                return False
                
            url = f"{self.base_url}/api/v1/{token}/telemetry"
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Prepare payload
            if isinstance(data, dict):
                payload = data
            else:
                payload = {device_name: data}
            
            # Add timestamp
            payload["ts"] = int(time.time() * 1000)
            
            logger.debug(f"Sending telemetry for {device_name}: {payload}")
            
            response = self.session.post(
                url, 
                data=json.dumps(payload),
                headers=headers,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"HTTPS telemetry SENT for {device_name}")
                return True
            else:
                logger.error(f"HTTPS telemetry FAILED for {device_name}: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL error for {device_name}: {str(e)}")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error for {device_name}: {str(e)}")
            return False
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout for {device_name}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"HTTPS telemetry exception for {device_name}: {str(e)}")
            return False

class SensorMonitor:
    def __init__(self):
        # Initialize GPIO for ultrasonic sensor from config
        GPIO.setmode(GPIO.BCM)
        self.GPIO_TRIG = config['hardware']['gpio_trig_pin']
        self.GPIO_ECHO = config['hardware']['gpio_echo_pin']
        GPIO.setup(self.GPIO_TRIG, GPIO.OUT)
        GPIO.setup(self.GPIO_ECHO, GPIO.IN)

        # Initialize I2C and ADS1115 from config
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.ads = ADS.ADS1115(self.i2c, address=config['hardware']['ads1115_address'])

        # Initialize HTTP client for ThingsBoard
        self.http_client = ThingsBoardHTTPClient()
        
        # Initialize UPS Monitor from config
        self.ups_monitor = UPSMonitor(
            shutdown_threshold=config['ups']['shutdown_threshold'],
            check_interval=config['ups']['check_interval']
        )
        
        # Sensor data cache
        self.sensor_data = {
            "temperature": None,
            "pH": None,
            "conductivity": None,
            "level": None,
            "recirc_pump": None,
            "dispense_pump": None,
            "ups_battery": None
        }

        # Timer control
        self._timer = None
        self._ups_timer = None
        self._running = False

    def get_filtered_sample(self, sensor_func, samples=SAMPLE_COUNT, discard=DISCARD_COUNT):
        """
        Take multiple samples, discard outliers, and return average of middle values
        """
        try:
            readings = []
            for _ in range(samples):
                reading = sensor_func()
                if reading is not None:
                    readings.append(reading)
                time.sleep(0.1)
            
            if len(readings) < (samples - discard*2):
                logger.warning(f"Insufficient valid samples: {len(readings)}/{samples}")
                return None
                
            readings.sort()
            valid_samples = readings[discard:-discard] if discard > 0 else readings
            return round(statistics.mean(valid_samples), 2)
        except Exception as e:
            logger.error(f"Filtered sampling error: {str(e)}")
            return None

    def measure_ph(self):
        """Measure pH value with outlier rejection"""
        def ph_measurement():
            chan = AnalogIn(self.ads, ADS.P0)
            voltage = chan.voltage
            # Use calibration formula from config
            return round((-6.02987 * voltage + 21.91), 2)
        
        self.sensor_data["pH"] = self.get_filtered_sample(ph_measurement)

    def measure_conductivity(self):
        """Measure conductivity with outlier rejection"""
        def conductivity_measurement():
            chan = AnalogIn(self.ads, ADS.P1)
            voltage = chan.voltage
            # Use calibration formula from config
            return round((779.3 * voltage - 302.46), 0)
        
        self.sensor_data["conductivity"] = self.get_filtered_sample(conductivity_measurement)

    def measure_rpump(self):
        """Measure recirculating pump with outlier rejection"""
        def rpump_measurement():
            chan = AnalogIn(self.ads, ADS.P2)
            voltage = round(chan.voltage, 2)
            # Use calibration formula from config
            return round(abs((voltage - 2.57)) * 6000, 0)
        
        self.sensor_data["recirc_pump"] = self.get_filtered_sample(rpump_measurement)

    def measure_dpump(self):
        """Measure dispensing pump with outlier rejection"""
        def dpump_measurement():
            chan = AnalogIn(self.ads, ADS.P3)
            voltage = round(chan.voltage, 2)
            # Use calibration formula from config
            return round(abs((voltage - 2.56)) * 4000, 0)
        
        self.sensor_data["dispense_pump"] = self.get_filtered_sample(dpump_measurement)

    def measure_water_level(self):
        """Measure water level with outlier rejection"""
        def level_measurement():
            GPIO.output(self.GPIO_TRIG, GPIO.LOW)
            time.sleep(0.1)
            GPIO.output(self.GPIO_TRIG, GPIO.HIGH)
            time.sleep(0.00001)
            GPIO.output(self.GPIO_TRIG, GPIO.LOW)
            
            pulse_start = time.time()
            timeout = pulse_start + 0.04
            
            while GPIO.input(self.GPIO_ECHO) == 0 and pulse_start < timeout:
                pulse_start = time.time()
            
            pulse_end = time.time()
            while GPIO.input(self.GPIO_ECHO) == 1 and pulse_end < timeout:
                pulse_end = time.time()
            
            if pulse_start >= timeout or pulse_end >= timeout:
                return None
                
            pulse_duration = pulse_end - pulse_start
            dist = 83.0 - round(pulse_duration * 17150, 1)
            # Use calibration formula from config
            return round(dist * 46.72, 0)
        
        self.sensor_data["level"] = self.get_filtered_sample(level_measurement)

    def measure_temperature(self):
        """Measure temperature from DS18B20 sensor"""
        try:
            sensor = DS18B20()
            temp = sensor.get_temperature()
            if temp is not None:
                # Use calibration offset from config
                self.sensor_data["temperature"] = round((temp + config['calibration']['temperature_offset']), 1)
            else:
                self.sensor_data["temperature"] = None
        except Exception as e:
            logger.error(f"Temperature measurement error: {str(e)}")
            self.sensor_data["temperature"] = None

    def measure_ups_battery(self):
        """Measure UPS battery level"""
        battery_level = self.ups_monitor.get_battery_level()
        self.sensor_data["ups_battery"] = battery_level
        return battery_level

    def send_pushover_notification(self, message):
        """Send notification via Pushover with SSL context"""
        try:
            context = ssl.create_default_context()
            conn = http.client.HTTPSConnection("api.pushover.net:443", context=context)
            conn.request("POST", "/1/messages.json",
                urllib.parse.urlencode({
                    "token": PUSHOVER_TOKEN,
                    "user": PUSHOVER_USER,
                    "message": message,
                }), {"Content-type": "application/x-www-form-urlencoded"})
            response = conn.getresponse()
            if response.status != 200:
                logger.error(f"Pushover notification failed: {response.status} {response.reason}")
            else:
                logger.info("Pushover notification sent successfully")
            conn.close()
        except Exception as e:
            logger.error(f"Failed to send Pushover notification: {str(e)}")

    def check_alerts(self):
        """Check sensor readings against thresholds"""
        alerts = []
        
        if self.sensor_data["temperature"] is not None:
            if self.sensor_data["temperature"] < ALERT_THRESHOLDS["temp_low"]:
                alerts.append(f"Temperature low: {self.sensor_data['temperature']}°C")
            elif self.sensor_data["temperature"] > ALERT_THRESHOLDS["temp_high"]:
                alerts.append(f"Temperature high: {self.sensor_data['temperature']}°C")
        
        if self.sensor_data["pH"] is not None:
            if self.sensor_data["pH"] < ALERT_THRESHOLDS["ph_low"]:
                alerts.append(f"pH low: {self.sensor_data['pH']}")
            elif self.sensor_data["pH"] > ALERT_THRESHOLDS["ph_high"]:
                alerts.append(f"pH high: {self.sensor_data['pH']}")
        
        if self.sensor_data["level"] is not None:
            if self.sensor_data["level"] < ALERT_THRESHOLDS["level_low"]:
                alerts.append(f"Water level low: {self.sensor_data['level']}L")
            elif self.sensor_data["level"] > ALERT_THRESHOLDS["level_high"]:
                alerts.append(f"Water level high: {self.sensor_data['level']}L")
        
        if self.sensor_data["recirc_pump"] is not None:
            if self.sensor_data["recirc_pump"] < ALERT_THRESHOLDS["pump_current_low"]:
                alerts.append(f"Recirc pump current low: {self.sensor_data['recirc_pump']}RPM")
        
        if self.sensor_data["dispense_pump"] is not None:
            if self.sensor_data["dispense_pump"] < ALERT_THRESHOLDS["pump_current_low"]:
                alerts.append(f"Dispense pump current low: {self.sensor_data['dispense_pump']}RPM")
        
        # Check UPS battery alert (warning level, not shutdown level)
        if self.sensor_data["ups_battery"] is not None:
            if self.sensor_data["ups_battery"] <= ALERT_THRESHOLDS["ups_battery_low"]:
                alerts.append(f"UPS Battery low: {self.sensor_data['ups_battery']}%")
        
        if alerts:
            alert_message = "RAS Alerts:\n" + "\n".join(alerts)
            logger.warning(f"Alerts triggered: {alert_message}")
            self.send_pushover_notification(alert_message)

    def log_data(self):
        """Log data to CSV file"""
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d,%H:%M:%S")
            data = [
                self.sensor_data["temperature"] or "",
                self.sensor_data["pH"] or "",
                self.sensor_data["conductivity"] or "",
                self.sensor_data["level"] or "",
                self.sensor_data["recirc_pump"] or "",
                self.sensor_data["dispense_pump"] or "",
                self.sensor_data["ups_battery"] or ""
            ]
            csv_line = f"{timestamp},{','.join(map(str, data))}\n"
            
            with open(config['paths']['data_file'], 'a') as f:
                f.write(csv_line)
            
            # Backup data
            shutil.copy(config['paths']['data_file'], config['paths']['backup_path'])
        except Exception as e:
            logger.error(f"Data logging failed: {str(e)}")

    def publish_data(self):
        """Publish all sensor data via HTTPS API"""
        try:
            success_count = 0
            total_sensors = 0
            
            for sensor_name, value in self.sensor_data.items():
                if value is not None:
                    total_sensors += 1
                    success = self.http_client.send_telemetry(sensor_name, value)
                    if success:
                        success_count += 1
                    # Small delay to avoid rate limiting
                    time.sleep(0.5)
                    
            if success_count > 0:
                logger.info(f"Data published via HTTPS API ({success_count}/{total_sensors} sensors)")
            else:
                logger.error(f"All HTTPS telemetry attempts failed ({total_sensors} sensors)")
                
        except Exception as e:
            logger.error(f"HTTPS publish failed: {str(e)}")

    def ups_monitoring_cycle(self):
        """UPS battery monitoring cycle"""
        if not self._running:
            return
            
        try:
            # Check UPS battery level
            battery_ok = self.ups_monitor.check_battery()
            
            # If battery is critical and shutdown was triggered, stop everything
            if not battery_ok:
                self.stop()
                return
                
        except Exception as e:
            logger.error(f"UPS monitoring error: {e}")
        finally:
            if self._running:
                self._ups_timer = Timer(UPS_CHECK_INTERVAL, self.ups_monitoring_cycle)
                self._ups_timer.start()

    def collect_and_publish(self):
        """Main collection and publishing routine"""
        if not self._running:
            return
            
        try:
            logger.info("Starting sensor data collection cycle")
            
            # Measure all sensors including UPS battery
            self.measure_temperature()
            self.measure_ph()
            self.measure_conductivity()
            self.measure_rpump()
            self.measure_dpump()
            self.measure_water_level()
            self.measure_ups_battery()

            # Process data
            self.publish_data()
            self.log_data()
            self.check_alerts()

            logger.info(f"Completed cycle at {datetime.datetime.now()}")
        except Exception as e:
            logger.error(f"Collection cycle failed: {str(e)}")
        finally:
            if self._running:
                self._timer = Timer(PUBLISH_INTERVAL, self.collect_and_publish)
                self._timer.start()

    def run(self):
        """Start the monitoring system"""
        self._running = True
        logger.info("Starting RAS monitoring system with UPS protection")
        logger.info(f"UPS shutdown threshold: {UPS_SHUTDOWN_THRESHOLD}%")
        
        # Start both monitoring cycles
        self.collect_and_publish()
        self.ups_monitoring_cycle()

    def stop(self):
        """Stop the monitoring system"""
        self._running = False
        if self._timer:
            self._timer.cancel()
        if self._ups_timer:
            self._ups_timer.cancel()
        logger.info("Monitoring system stopped")

    def cleanup(self):
        """Clean up resources"""
        self.stop()
        GPIO.cleanup()
        logger.info("System cleanup completed")

if __name__ == "__main__":
    monitor = SensorMonitor()
    try:
        monitor.run()
        while monitor._running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
    finally:
        monitor.cleanup()