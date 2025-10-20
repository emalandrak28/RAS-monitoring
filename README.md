Comprehensive Description: RAS (Recirculating Aquaculture System) Monitor

This Python script implements a robust, multi-sensor monitoring and alerting system designed for a Recirculating Aquaculture System (RAS). It runs on a Raspberry Pi and continuously tracks critical water quality and equipment parameters, publishing the data to a cloud dashboard (ThingsBoard) and triggering instant mobile alerts if any values fall outside safe operating ranges. The system works with a UPS HAT (battery) to perform even with current shortages

Core Purpose and Functionality

The primary purpose of this system is to automate the monitoring of a sensitive aquatic environment. It performs the following key functions in a continuous loop:
-   Data Acquisition: Reads from multiple analog and digital sensors.
-   Data Processing: Applies statistical filtering to reduce noise and outliers.
-   Data Logging: Saves all readings with timestamps to a local CSV file and creates a backup.
-   Data Transmission: Publishes each sensor's data to its respective device on a ThingsBoard IoT platform via  via HTTPS
-   Alerting: Evaluates readings against predefined thresholds and sends immediate push notifications to a phone via Pushover if any parameter is out of bounds.
-   Robustness: Includes comprehensive error handling and logging to ensure long-term, unattended operation.

 2. Key Components and Technologies

-   Hardware Platform: Raspberry Pi.
-   Sensors:
    -   DS18B20: A digital temperature sensor for water temperature.
    -   ADS1115: A 16-bit ADC (Analog-to-Digital Converter) used to read multiple analog sensors with high precision.
    -   pH Probe: Connected to the ADS1115. Voltage is converted to pH value.
    -   Conductivity Probe: Connected to the ADS1115. Voltage is converted to conductivity value.
    -   Ultrasonic Sensor (JSN-SR04T): Used for non-contact water level measurement in a sump or tank. GPIO pins trigger and read the echo.
    -   Current Sensors (ACS712-20A): Two sensors connected to the ADS1115 to measure the current draw (and infer operation) of the recirculating and dispensing pumps.
-   Software/Protocols:
    -   HTTPS: A lightweight messaging protocol for IoT. Used to send data to ThingsBoard.
    -   ThingsBoard: An open-source IoT platform for data visualization, storage, and device management.
    -   Pushover: A service for sending real-time push notifications to mobile devices.
    -   Logging Module: For detailed, timestamped status updates and error tracking.

3. Workflow and Process Flow

1.  Start: The script is executed. The `SensorMonitor` object is created, initializing all hardware and MQTT (or HTTPS) connections.
2.  Loop Begins: The `collect_and_publish` method is called.
3.  Data Collection: Each sensor is read multiple times, and a filtered average is computed.
4.  Data Transmission: The filtered data is published to ThingsBoard and logged to the CSV file.
5.  Alert Check: Values are checked against thresholds. Alerts are sent immediately if needed.
6.  Wait: The method schedules itself to run again after a 10-minute sleep.
7.  Termination: If a keyboard interrupt (Ctrl+C) or error occurs, the loop is stopped, and resources are cleaned up gracefully. The system closes if UPS battery falls under 10%.

 5. Configuration and Constants

The script is highly configurable through constants defined at files RAS.env and config.yaml
-   Cloud Settings: ThingsBoard host, topic, and device access tokens.
-   Alerting Settings: Pushover API keys and user key.
-   Timing: Interval between readings (`PUBLISH_INTERVAL`).
-   Data Quality: Number of samples to take and discard (`SAMPLE_COUNT`, `DISCARD_COUNT`).
-   Safety Thresholds: All operational thresholds (`ALERT_THRESHOLDS`) for temperature, pH, water level, and pump current are defined here for easy adjustment.

 6. Error Handling and Logging

The script is designed for reliability:
-   Try-Except Blocks: Nearly all operations are wrapped in try-except blocks to prevent a single sensor failure from crashing the entire system.
-   Comprehensive Logging: The `logging` module is configured to output messages to both the console and a log file (`ras_monitor.log`). This is essential for debugging issues in a headless production environment.

 7. Conclusion

This code represents a complete, professional-grade IoT solution for environmental monitoring. It effectively bridges the physical world (sensors) with the digital cloud (ThingsBoard) and provides a critical safety net with its instant alerting system. Its modular class-based design, focus on data integrity through filtering, and extensive error handling make it a robust application suitable for a 24/7 operational context like aquaculture.
