"""
A Python adapter module for Roomba Open Interface

This module is based on the document: iRobot® Roomba 500 Open Interface (OI) Specification
- Link https://www.irobot.lv/uploaded_files/File/iRobot_Roomba_500_Open_Interface_Spec.pdf
"""

import math
from time import sleep
from typing import Any, Dict

import serial  # pyserial

from dt_roomba.roomba.constants import (
    ROOMBA_COMMANDS,
    ROOMBA_PARAMS,
    ROOMBA_SENSORS,
)


class PyRoombaAdapter:
    """
    Adapter class for Roomba Open Interface

    The constructor connects serial port and change the mode to safe mode

    :param string port: Serial port path

    :param int bau_rate: bau rate of serial connection (default=115200)

    :param float time_out_sec: read time out of serial connection [sec] (default=1.0)

    :param float wheel_span_mm: wheel span of Roomba [mm]  (default=235.0)

    Examples:
        >>> PORT = "/dev/ttyUSB0"
        >>> adapter = PyRoombaAdapter(PORT)

    """

    def __init__(self, port, bau_rate=115200, time_out_sec=1.0, wheel_span_mm=235.0):
        self.WHEEL_SPAN = wheel_span_mm
        try:
            self.serial_con = self._connect_serial(port, bau_rate, time_out_sec)
        except serial.SerialException as exc:
            raise ConnectionError(
                f"Cannot find serial port ('{port}'). Please reconnect it."
            ) from exc

        self.change_mode_to_safe()  # default mode is safe mode
        self.stream_sensors = {}

        # Robot state variables
        self.oi_mode = None
        self.charging_state = None
        self.voltage = None
        self.current = None
        self.temperature = None
        self.battery_charge = None
        self.battery_capacity = None
        self.distance = None
        self.angle = None
        self.encoder_left = None
        self.encoder_right = None

        sleep(1.0)

    def __del__(self):
        """
        Destructor of PyRoombaAdapter class

        The Destructor make Roomba move to passive mode and close serial connection
        """
        # The `self.serial_con` attribute may not exist if an exception is
        # encountered in `__init__()`
        if getattr(self, "serial_con", None):
            # Only send command if port is open
            if self.serial_con.isOpen():
                # disconnect sequence
                self._send_cmd(ROOMBA_COMMANDS.START)
                sleep(0.1)
            self.serial_con.close()

    def start_cleaning(self):
        """
        Start the default cleaning

        - Available in modes: Passive, Safe, or Full
        - Changes mode to: Passive

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.start_cleaning()
        """
        self._send_cmd(ROOMBA_COMMANDS.START)
        sleep(0.1)
        self._send_cmd(ROOMBA_COMMANDS.CLEAN)

    def start_max_cleaning(self):
        """
        Start the max cleaning

        - Available in modes: Passive, Safe, or Full
        - Changes mode to: Passive

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.start_max_cleaning()
        """
        self._send_cmd(ROOMBA_COMMANDS.START)
        sleep(0.1)
        self._send_cmd(ROOMBA_COMMANDS.MAX)

    def start_spot_cleaning(self):
        """
        Start spot cleaning

        - Available in modes: Passive, Safe, or Full
        - Changes mode to: Passive

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.start_spot_cleaning()
        """
        self._send_cmd(ROOMBA_COMMANDS.START)
        sleep(0.1)
        self._send_cmd(ROOMBA_COMMANDS.SPOT)

    def start_seek_dock(self):
        """
        Start seek dock

        - Available in modes: Passive, Safe, or Full
        - Changes mode to: Passive

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.start_seek_dock()
        """
        self._send_cmd(ROOMBA_COMMANDS.START)
        sleep(0.1)
        self._send_cmd(ROOMBA_COMMANDS.SEEK_DOCK)

    def change_mode_to_passive(self):
        """
        Change mode to passive mode

        Roomba beeps once to acknowledge it is starting from "off" mode.

        - Available in modes: Passive, Safe, or Full
        """
        self._send_cmd(ROOMBA_COMMANDS.START)

    def change_mode_to_safe(self):
        """
        Change mode to safe mode

        Safe mode turns off all LEDs.
        If a safety condition occurs, Roomba reverts automatically to Passive mode.

        - Available in modes: Passive, Safe, or Full
        """
        # send command
        self._send_cmd(ROOMBA_COMMANDS.START)
        self._send_cmd(ROOMBA_COMMANDS.SAFE)

    def change_mode_to_full(self):
        """
        Change mode to full mode

        Full mode turns off the cliff, wheel-drop and internal charger safety features.
        In Full mode, Roomba executes any command that you send it, even if the internal charger is plugged in,
        or command triggers a cliff or wheel drop condition.

        - Available in modes: Passive, Safe, or Full
        """
        # send command
        self._send_cmd(ROOMBA_COMMANDS.START)
        self._send_cmd(ROOMBA_COMMANDS.FULL)

    def turn_off_power(self):
        """
        Turn off power of Roomba

        The mode change to passive mode.

        - Available in modes: Passive, Safe, or Full

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.turn_off_power()
        """
        # send command
        self._send_cmd(ROOMBA_COMMANDS.START)
        self._send_cmd(ROOMBA_COMMANDS.POWER)

    def request_data(self, request_id_list):
        if len(request_id_list) == 1:  # single packet
            self._send_cmd(ROOMBA_COMMANDS.START)
            self._send_cmd(ROOMBA_COMMANDS.SENSORS)
            self._send_cmd(request_id_list[0])
            sleep(0.5)
            print("re:", self.serial_con.read())
            sleep(0.5)

    def move(self, velocity, yaw_rate):
        """
        control roomba at the velocity and the rotational speed (yaw rate)

        Note:
            The Roomba keep a control command until receiving next command

        - Available in modes: Safe or Full
        - Changes mode to: No Change

        - Special cases:
            - Straight = 32768 or 32767 = hex 8000 or 7FFF
            - Turn in place clockwise = -1
            - Turn in place counter-clockwise = 1

        :param float velocity: velocity (m/s) (-0.5 - 0.5)

        :param float yaw_rate: rotational velocity (rad/s)
        """
        if velocity == 0:  # rotation
            vel_mm_sec = math.fabs(yaw_rate) * (self.WHEEL_SPAN / 2.0)
            if yaw_rate >= 0:
                radius_mm = 1
            else:  # default is 'CCW' (turning left)
                radius_mm = -1
        elif yaw_rate == 0:
            vel_mm_sec = velocity * 1000.0  # m/s -> mm/s
            radius_mm = ROOMBA_PARAMS.STRAIGHT_RADIUS
        else:
            vel_mm_sec = velocity * 1000.0  # m/s -> mm/s
            radius_mm = vel_mm_sec / yaw_rate

        self.send_drive_cmd(vel_mm_sec, int(radius_mm))

    def send_drive_cmd(self, velocity, radius):
        """
        send drive command

        This command controls Roomba's drive wheels. It takes four data bytes, which are interpreted as two 16-bit
        signed values using two's complement. The first two bytes specify the average velocity of the drive wheels
        in millimeters per second (mm/s), with a range of -500 – 500 mm/s. The next two bytes specify the radius
        in millimeters at which Roomba will turn. The longer radii make Roomba drive straighter, while the shorter
        radii make Roomba turn more acutely. The radius is interpreted as a two's-complement integer. This means
        that a radius of 32768 mm (or 32767 mm) makes Roomba drive in a straight line.

        - Available in modes: Safe or Full

        :param int velocity: velocity [mm/s] (-500 - 500)

        :param int radius: radius [mm] (-2000 – 2000 | 32768 = straight)

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.send_drive_cmd(100, 32768)  # move straight forward with 100mm/s
            >>> sleep(2.0)
        """
        velocity = self._adjust_min_max(
            velocity, ROOMBA_PARAMS.MIN_VELOCITY, ROOMBA_PARAMS.MAX_VELOCITY
        )
        vel_high, vel_low = self._get_2_bytes(velocity)

        # Adjust radius
        if radius == 0:
            pass
        elif 0 < radius < ROOMBA_PARAMS.MIN_RADIUS:
            radius = ROOMBA_PARAMS.MIN_RADIUS
        elif radius > ROOMBA_PARAMS.MAX_RADIUS:
            radius = ROOMBA_PARAMS.MAX_RADIUS
        elif ROOMBA_PARAMS.MAX_RADIUS < radius < ROOMBA_PARAMS.STRAIGHT_RADIUS:
            radius = ROOMBA_PARAMS.STRAIGHT_RADIUS

        radius_high, radius_low = self._get_2_bytes(radius)

        # send these bytes and set the stored velocities
        self._send_cmd(
            [ROOMBA_COMMANDS.DRIVE, vel_high, vel_low, radius_high, radius_low]
        )

    def send_drive_direct(self, right_velocity, left_velocity):
        """
        send drive direct command

        This command lets you control the forward and backward motion of Roomba's drive wheels independently.
        It takes four data bytes, which are interpreted as two 16-bit signed values using two's complement.
        A positive velocity makes that wheel drive forward, while a negative velocity makes it drive backward.

        - Available in modes: Safe or Full

        :param int right_velocity: Right wheel velocity (-500 - 500) [mm/s]

        :param int left_velocity: Left wheel velocity (-500 - 500) [mm/s]

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.send_drive_direct(100, -100) # rotate in place
            >>> sleep(2.0)
        """
        right_velocity = self._adjust_min_max(
            right_velocity, ROOMBA_PARAMS.MIN_VELOCITY, ROOMBA_PARAMS.MAX_VELOCITY
        )
        right_high, right_low = self._get_2_bytes(right_velocity)

        left_velocity = self._adjust_min_max(
            left_velocity, ROOMBA_PARAMS.MIN_VELOCITY, ROOMBA_PARAMS.MAX_VELOCITY
        )
        left_high, left_low = self._get_2_bytes(left_velocity)

        # send these bytes and set the stored velocities
        self._send_cmd(
            [ROOMBA_COMMANDS.DRIVE_DIRECT, right_high, right_low, left_high, left_low]
        )

    def send_drive_pwm(self, right_pwm, left_pwm):
        """
        send drive pwm command

        This command lets you control the forward and backward motion of Roomba's drive wheels independently.
        It takes four data bytes, which are interpreted as two 16-bit signed values using two's complement.
        A positive PWM makes that wheel drive forward, while a negative PWM makes it drive backward.

        - Available in modes: Safe or Full

        :param int right_pwm: Right wheel PWM (-255 – 255)

        :param int left_pwm: Left wheel PWM (-255 - 255)

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.send_drive_pwm(-200, -200) # move backward
            >>> sleep(2.0) # keep 2 sec
        """
        right_pwm = self._adjust_min_max(right_pwm, -255, 255)
        right_high, right_low = self._get_2_bytes(right_pwm)

        left_pwm = self._adjust_min_max(left_pwm, -255, 255)
        left_high, left_low = self._get_2_bytes(left_pwm)

        # send these bytes and set the stored velocities
        self._send_cmd(
            [ROOMBA_COMMANDS.DRIVE_PWM, right_high, right_low, left_high, left_low]
        )

    def send_moters_cmd(
        self,
        main_brush_on,
        main_brush_direction_is_ccw,
        side_brush_on,
        side_brush_direction_is_inward,
        vacuum_on,
    ):
        """
        send moters command

        This command controls the motion of Roomba's main brush, side brush, and vacuum independently.
        Motor velocity cannot be controlled with this command, all motors will run at maximum speed when enabled.
        The main brush and side brush can be run in either direction. The vacuum only runs forward.

        :param bool main_brush_on: main brush on or off

        :param bool main_brush_direction_is_ccw: main brush direction, clockwise or counter-clockwise(default)

        :param bool side_brush_on: side brush on or off

        :param bool side_brush_direction_is_inward: side brush direction, inward(default) or outward

        :param bool side_brush_on: side brush on or off

        :param bool vacuum_on: vacuum on or off

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.send_moters_cmd(False, True, True, True, False) # side brush is on, and it rotates inward
            >>> sleep(2.0) # keep 2 sec
        """
        cmd = 0  # All initial bit is 0
        if side_brush_on:
            cmd |= 0b00000001
        if vacuum_on:
            cmd |= 0b00000010
        if main_brush_on:
            cmd |= 0b00000100
        if not side_brush_direction_is_inward:
            cmd |= 0b00001000
        if not main_brush_direction_is_ccw:
            cmd |= 0b00010000

        self._send_cmd([ROOMBA_COMMANDS.MOTORS, cmd])

    def send_pwm_moters(self, main_brush_pwm, side_brush_pwm, vacuum_pwm):
        """
        send pwm moters

        This command control the speed of Roomba's main brush, side brush, and vacuum independently.
        With each data byte, you specify the duty cycle for the low side driver (max 128).
        For example, if you want to control a motor with 25% of battery voltage, choose a duty cycle of 128 * 25% = 32.
        The main brush and side brush can be run in either direction.
        The vacuum only runs forward. Positive speeds turn the motor in its default (cleaning) direction.
        Default direction for the side brush is counterclockwise.
        Default direction for the main brush/flapper is inward.

        - Available in modes: Safe or Full

        :param int main_brush_pwm: main brush PWM (-127 - 127)

        :param int side_brush_pwm: side brush PWM (-127 - 127)

        :param int vacuum_pwm: vacuum duty cycle (0 - 127)

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.send_pwm_moters(50, 50, 50)
            >>> sleep(2.0) # keep 2 sec
        """
        main_brush_pwm = self._adjust_min_max(main_brush_pwm, -127, 127)
        main_brush_pwm = self._get_1_bytes(main_brush_pwm)

        side_brush_pwm = self._adjust_min_max(side_brush_pwm, -127, 127)
        side_brush_pwm = self._get_1_bytes(side_brush_pwm)

        vacuum_pwm = self._adjust_min_max(vacuum_pwm, 0, 127)
        vacuum_pwm = self._get_1_bytes(vacuum_pwm)

        self._send_cmd(
            [ROOMBA_COMMANDS.PWM_MOTORS, main_brush_pwm, side_brush_pwm, vacuum_pwm]
        )

    def send_buttons_cmd(
        self,
        clean=False,
        spot=False,
        dock=False,
        minute=False,
        hour=False,
        day=False,
        schedule=False,
        clock=False,
    ):
        """
        send buttons command

        This command lets you push Roomba's buttons. The buttons request specific cleaning modes.

        :param bool clean: clean button is pressed

        :param bool spot: spot button is pressed

        :param bool dock: dock button is pressed

        :param bool minute: minute button is pressed

        :param bool hour: hour button is pressed

        :param bool day: day button is pressed

        :param bool schedule: schedule button is pressed

        :param bool clock: clock button is pressed

        Examples:
            >>> PORT = "/dev/ttyUSB0"
            >>> adapter = PyRoombaAdapter(PORT)
            >>> adapter.send_buttons_cmd(True, False, False, False, False, False, False, False) # press clean button
            >>> sleep(2.0) # keep 2 sec
        """
        cmd = 0  # All initial bit is 0
        if clean:
            cmd |= 0b00000001
        if spot:
            cmd |= 0b00000010
        if dock:
            cmd |= 0b00000100
        if minute:
            cmd |= 0b00001000
        if hour:
            cmd |= 0b00010000
        if day:
            cmd |= 0b00100000
        if schedule:
            cmd |= 0b01000000
        if clock:
            cmd |= 0b10000000

        self._send_cmd([ROOMBA_COMMANDS.BUTTONS, cmd])

    def send_song_cmd(
        self, song_number, song_length, note_number_list, note_duration_list
    ):
        """
        send song command

        This command lets you specify a song to the OI to play.

        :param int song_number: Song index [0-4]

        :param int song_length: Number of notes in song (1-16)

        :param list note_number_list: list of note numbers

        :param list note_duration_list: list of note durations

        Examples:
            >>> adapter = PyRoombaAdapter("/dev/ttyUSB0")
            >>> # note names
            >>> f4 = 65
            >>> a4 = 69
            >>> c5 = 72
            >>> # note lengths
            >>> MEASURE = 160
            >>> HALF = int(MEASURE / 2)
            >>> Q = int(MEASURE / 4)
            >>> Ed = int(MEASURE * 3 / 16)
            >>> S = int(MEASURE / 16)
            >>> adapter.send_song_cmd(0, 9,
            >>>             [a4, a4, a4, f4, c5, a4, f4, c5, a4],
            >>>             [Q, Q, Q, Ed, S, Q, Ed, S, HALF]) # set song
            >>> adapter.send_play_cmd(0) # play song
            >>> sleep(10.0) # keep playing
        """
        cmd = [ROOMBA_COMMANDS.SONG, song_number, song_length]
        for note_number, note_duration in zip(note_number_list, note_duration_list):
            cmd.append(note_number)
            cmd.append(note_duration)
        self._send_cmd(cmd)

    def send_play_cmd(self, song_number):
        """
        send play command

        This command lets you select a song to play from the songs added to Roomba using the Song command.
        You must add one or more songs to Roomba using the Song command in order for the Play command to work.

        :param int song_number: (0-4)

        Examples:
            >>> adapter = PyRoombaAdapter("/dev/ttyUSB0")
            >>> # note names
            >>> f4 = 65
            >>> a4 = 69
            >>> c5 = 72
            >>> # note lengths
            >>> MEASURE = 160
            >>> HALF = int(MEASURE / 2)
            >>> Q = int(MEASURE / 4)
            >>> Ed = int(MEASURE * 3 / 16)
            >>> S = int(MEASURE / 16)
            >>> adapter.send_song_cmd(0, 9,
            >>>             [a4, a4, a4, f4, c5, a4, f4, c5, a4],
            >>>             [Q, Q, Q, Ed, S, Q, Ed, S, HALF]) # set song
            >>> adapter.send_play_cmd(0) # play song
            >>> sleep(10.0) # keep playing
        """
        self._send_cmd([ROOMBA_COMMANDS.PLAY, song_number])

    def _request_sensor(self, sensor):
        """
        Requests sensor value from Roomba and returns interpreted value.
        :param sensor: Sensor tuple (packet_id, data_bytes, signed)
        :return: int sensor value
        """
        self._send_cmd([ROOMBA_COMMANDS.SENSORS, sensor[0]])
        raw = self.serial_con.read(sensor[1])
        return int.from_bytes(raw, byteorder="big", signed=sensor[2])

    def request_charging_state(self):
        """
        requests charging state

        This function returns one of following Roomba's current charging states:
        0 = Not charging,
        1 = Reconditioning Charging,
        2 = Full Charging,
        3 = Trickle Charging,
        4 = Waiting,
        5 = Charging Fault Condition

        :return: State 0-5
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.charging_state
        else:  # No stream, read directly from sensor
            self.charging_state = self._request_sensor(ROOMBA_SENSORS.CHARGING_STATE)
            return self.charging_state

    def request_voltage(self):
        """
        requests battery voltage

        This function returns the current voltage of Roomba's battery in millivolts (mV).

        :return: Voltage in range: 0 – 65535 mV
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.voltage
        else:  # No stream, read directly from sensor
            self.voltage = self._request_sensor(ROOMBA_SENSORS.VOLTAGE)
            return self.voltage

    def request_current(self):
        """
        requests battery current

        The current in milliamps (mA) flowing into or out of Roomba's battery. Negative currents indicate that the
        current is flowing out of the battery, as during normal running. Positive currents indicate that the current
        is flowing into the battery, as during charging.

        :return: Current in range: -32768 – 32767 mA
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.current
        else:  # No stream, read directly from sensor
            self.current = self._request_sensor(ROOMBA_SENSORS.CURRENT)
            return self.current

    def request_temperature(self, celsius=True):
        """
        requests battery temperature

        This command requests the temperature of Roomba's battery in degrees Celsius.

        :param bool celsius: true if celsius, false for fahrenheit

        :return: Temperature in range: -128 – 127 Celsius or converted in Fahrenheit
        """
        if len(self.stream_sensors) > 0:
            temp = self.temperature
        else:
            self.temperature = self._request_sensor(ROOMBA_SENSORS.TEMPERATURE)
            temp = self.temperature

        if not celsius and temp is not None:
            temp = (temp * 1.8) + 32
        return temp

    def request_charge(self):
        """
        requests battery charge

        Returns the current charge of Roomba's battery in milliamp-hours (mAh). The charge value decreases as the
        battery is depleted during running and increases when the battery is charged.

        :return: Charge in range: 0 – 65535 mAh
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.battery_charge
        else:  # No stream, read directly from sensor
            self.battery_charge = self._request_sensor(ROOMBA_SENSORS.BATTERY_CHARGE)
            return self.battery_charge

    def request_capacity(self):
        """
        requests estimated battery capacity

        Returns the estimated charge capacity of Roomba's battery in milliamp-hours (mAh).

        :return: Capacity in range: 0 – 65535 mAh
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.battery_capacity
        else:  # No stream, read directly from sensor
            self.battery_capacity = self._request_sensor(
                ROOMBA_SENSORS.BATTERY_CAPACITY
            )
            return self.battery_capacity

    def request_distance(self):
        """
        Requests the Roomba's total distance traveled since it was last checked.

        Returns distance traveled in millimeters (mm) since the distance was last requested. Average between both
        wheel distances.

        :return: Output range ± 32768 mm
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.distance
        else:  # No stream, read directly from sensor
            self.distance = self._request_sensor(ROOMBA_SENSORS.DISTANCE)
            return self.distance

    def request_angle(self):
        """
        Requests the angular displacement since it was last checked.

        Returns the angle in radians that the Roomba has turned since the angle was last requested.
        Counter-clockwise is defined positive.

        :return: Output range ± 571.9 rad
        """
        if len(self.stream_sensors) > 0:
            angle = self.angle
        else:
            # Return a value in radians to remain consistent with the rest of the library
            self.angle = math.radians(self._request_sensor(ROOMBA_SENSORS.ANGLE))
            angle = self.angle
        return angle

    def request_encoder_counts(self):
        """
        Requests the raw Roomba encoder counts.

        Returns a tuple containing the following: (left count, right count).
        Linear distance traversed by each wheel is defined by the following:
        distance = (π * 72.0 / 508.8) * count.

        :return: A tuple of (left count, right count) in range -32767 - 32768
        """
        if len(self.stream_sensors) > 0:
            return (self.encoder_left, self.encoder_right)
        else:
            self.encoder_left = self._request_sensor(ROOMBA_SENSORS.LEFT_ENCODER_COUNTS)
            self.encoder_right = self._request_sensor(
                ROOMBA_SENSORS.RIGHT_ENCODER_COUNTS
            )
            return (self.encoder_left, self.encoder_right)

    def request_oi_mode(self):
        """
        requests corrent OI mode

        This function returns the current OI mode. Modes see in following table:
        0 = Off,
        1 = Passive,
        2 = Safe,
        3 = Full

        :return: State 0-3
        """
        if len(self.stream_sensors) > 0:  # Stream is active
            return self.oi_mode
        else:  # No stream, read directly from sensor
            self.oi_mode = self._request_sensor(ROOMBA_SENSORS.OI_MODE)
            return self.oi_mode

    def data_stream_start(self, sensors_list):
        """
        starts data stream

        This function issues command which starts a stream of data packets. The provided list of packets is sent
        every 15 ms, which is the rate Roomba uses to update data.

        :param list sensors_list: One or more from following sensors: ["Charging State", "Voltage", "Current", "Temperature", "Battery Charge",
                                "Battery Capacity", "OI Mode"]

        Examples:
            >>> adapter = PyRoombaAdapter("/dev/ttyUSB0")
            >>> adapter.start_data_stream(["Charging State", "Voltage", "Temperature"])
            >>> print(adapter.read_data_stream()) # list with values

        """
        self.stream_sensors = {}
        stream = []
        for sensor_name in sensors_list:
            # Get the sensor tuple from ROOMBA_SENSORS
            sensor_attr = sensor_name.upper().replace(" ", "_")
            sensor = getattr(ROOMBA_SENSORS, sensor_attr)
            self.stream_sensors.update(
                {
                    sensor[0]: (
                        sensor[1],
                        sensor[2],
                    )
                }
            )
            stream.append(sensor[0])

        if len(stream) > 0:
            request = [ROOMBA_COMMANDS.STREAM, len(stream)]
            request.extend(stream)
            self._send_cmd(request)

    def data_stream_stop(self):
        """
        stops data stream

        Stops data stream and resets sensors list.
        """
        self._send_cmd([ROOMBA_COMMANDS.STREAM, 0])
        self.stream_sensors = {}

    def data_stream_read(self):
        """
        reads data stream

        Reads stream of sensor data packets from Roomba.

        :return: list with sensors values or empty list on error

        Examples:
            >>> adapter = PyRoombaAdapter("/dev/ttyUSB0")
            >>> adapter.start_data_stream(["Charging State", "Voltage", "Temperature"])
            >>> print(adapter.read_data_stream()) # [0, 15530, 20]
        """
        # Packet ID to state variable mapping
        PACKET_ID_TO_STATE = {
            ROOMBA_SENSORS.CHARGING_STATE[0]: "charging_state",
            ROOMBA_SENSORS.VOLTAGE[0]: "voltage",
            ROOMBA_SENSORS.CURRENT[0]: "current",
            ROOMBA_SENSORS.TEMPERATURE[0]: "temperature",
            ROOMBA_SENSORS.BATTERY_CHARGE[0]: "battery_charge",
            ROOMBA_SENSORS.BATTERY_CAPACITY[0]: "battery_capacity",
            ROOMBA_SENSORS.DISTANCE[0]: "distance",
            ROOMBA_SENSORS.ANGLE[0]: "angle",
            ROOMBA_SENSORS.OI_MODE[0]: "oi_mode",
            ROOMBA_SENSORS.LEFT_ENCODER_COUNTS[0]: "encoder_left",
            ROOMBA_SENSORS.RIGHT_ENCODER_COUNTS[0]: "encoder_right",
        }

        result = []

        header_magic = self.serial_con.read(1)
        if header_magic == b"\x13":
            data_len = self.serial_con.read(1)
            data = self.serial_con.read(int.from_bytes(data_len, byteorder="big"))
            checksum = self.serial_con.read(1)
            calculated_checksum = sum(bytes(header_magic + data_len + data + checksum))
            if (calculated_checksum & 0xFF) == 0:
                data_pos = 0
                while data_pos < len(data):
                    packet_id = int.from_bytes(
                        data[data_pos : data_pos + 1], byteorder="big"
                    )
                    data_pos += 1

                    packet_size = self.stream_sensors[packet_id][0]
                    packet_data = data[data_pos : data_pos + packet_size]
                    data_pos += packet_size

                    value = int.from_bytes(
                        packet_data,
                        byteorder="big",
                        signed=self.stream_sensors[packet_id][1],
                    )
                    result.append(value)

                    # UPDATE STATE
                    if packet_id in PACKET_ID_TO_STATE:
                        state_var = PACKET_ID_TO_STATE[packet_id]
                        if state_var == "angle":
                            # Special case: angle needs to be converted to radians
                            setattr(self, state_var, math.radians(value))
                        else:
                            setattr(self, state_var, value)

        return result

    @staticmethod
    def _connect_serial(port, bau_rate, time_out):
        serial_con = serial.Serial(port, baudrate=bau_rate, timeout=time_out)
        if serial_con.isOpen():
            print("Serial port is open, presumably to a roomba...")
        else:
            print("Serial port did NOT open")
        return serial_con

    @staticmethod
    def _adjust_min_max(val, min_val, max_val):
        # integer cast
        if type(val) != int:
            val = int(val)

        if val < min_val:
            val = min_val
        elif val > max_val:
            val = max_val

        return val

    @staticmethod
    def _get_2_bytes(value):
        """returns two bytes (ints) in high, low order
        whose bits form the input value when interpreted in
        two's complement
        """
        # if positive or zero, it's OK
        if value >= 0:
            eqBitVal = value
        # if it's negative, I think it is this
        else:
            eqBitVal = (1 << 16) + value

        return (eqBitVal >> 8) & 0xFF, eqBitVal & 0xFF

    @staticmethod
    def _get_1_bytes(value):
        """returns one bytes (int)"""
        # if positive or zero, it's OK
        if value >= 0:
            eqBitVal = value
        # if it's negative, I think it is this
        else:
            eqBitVal = (1 << 8) + value
        return eqBitVal & 0xFF

    def _send_cmd(self, cmd):
        if type(cmd) == list:  # command list
            self.serial_con.write(bytes(cmd))
        else:  # one command
            self.serial_con.write(bytes([cmd]))

    def get_variables(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all state variables that this robot publishes.

        Returns a dictionary describing all available variables with their
        MQTT topics and metadata. Used for discovery by the cloud twin.

        Returns:
            Dict mapping variable name to metadata including mqtt_topic
        """
        return {
            "battery": {
                "name": "battery",
                "mqtt_topic": "roomba/battery",
                "data_type": "dict",
                "description": "Battery information (voltage, current, temperature, charge, capacity)",
            },
            "sensors": {
                "name": "sensors",
                "mqtt_topic": "roomba/sensors",
                "data_type": "dict",
                "description": "Sensor data (distance, angle, encoder_left, encoder_right)",
            },
            "mode": {
                "name": "mode",
                "mqtt_topic": "roomba/mode",
                "data_type": "dict",
                "description": "Robot mode information (oi_mode, charging_state)",
            },
        }

    def get_methods(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all methods/commands that this robot supports.

        Returns a dictionary describing all available methods with their
        MQTT command topics and parameters. Used for discovery by the cloud twin.

        Returns:
            Dict mapping method name to metadata including mqtt_command_topic and parameters
        """
        return {
            "clean": {
                "name": "clean",
                "mqtt_command_topic": "roomba/clean",
                "description": "Start normal cleaning",
                "required_modes": ["passive", "safe", "full"],
                "mode_change_effect": "Changes the robot mode to passive.",
                "parameters": {},
            },
            "max_clean": {
                "name": "max_clean",
                "mqtt_command_topic": "roomba/max_clean",
                "description": "Start maximum cleaning",
                "required_modes": ["passive", "safe", "full"],
                "mode_change_effect": "Changes the robot mode to passive.",
                "parameters": {},
            },
            "spot_clean": {
                "name": "spot_clean",
                "mqtt_command_topic": "roomba/spot_clean",
                "description": "Start spot cleaning",
                "required_modes": ["passive", "safe", "full"],
                "mode_change_effect": "Changes the robot mode to passive.",
                "parameters": {},
            },
            "dock": {
                "name": "dock",
                "mqtt_command_topic": "roomba/dock",
                "description": "Return to dock",
                "required_modes": ["passive", "safe", "full"],
                "mode_change_effect": "Changes the robot mode to passive.",
                "parameters": {},
            },
            "mode": {
                "name": "mode",
                "mqtt_command_topic": "roomba/mode",
                "description": "Set robot operating mode",
                "required_modes": ["passive", "safe", "full"],
                "parameters": {
                    "mode": {
                        "type": "string",
                        "required": True,
                        "enum": ["passive", "safe", "full"],
                        "description": "Operating mode (passive, safe, or full)",
                        "constraints": {
                            "allowed_values": ["passive", "safe", "full"],
                        },
                    }
                },
            },
            "power_off": {
                "name": "power_off",
                "mqtt_command_topic": "roomba/power_off",
                "description": "Power off the robot",
                "required_modes": ["passive", "safe", "full"],
                "mode_change_effect": "Changes the robot mode to passive/powered off.",
                "parameters": {},
            },
            "move": {
                "name": "move",
                "mqtt_command_topic": "roomba/move",
                "description": "Move with velocity and angle",
                "required_modes": ["safe", "full"],
                "recommended_mode_before_command": "safe",
                "parameters": {
                    "velocity": {
                        "type": "float",
                        "required": True,
                        "description": "Velocity in m/s",
                        "constraints": {
                            "minimum": -0.5,
                            "maximum": 0.5,
                            "unit": "m/s",
                            "behavior": "Values outside this range are clamped by the robot adapter, but generated services should stay within range.",
                        },
                    },
                    "angle": {
                        "type": "float",
                        "required": True,
                        "description": "Yaw rate in rad/s",
                        "constraints": {
                            "unit": "rad/s",
                            "special_cases": {
                                "0": "drive straight when velocity is non-zero",
                            },
                        },
                    },
                },
            },
            "stop": {
                "name": "stop",
                "mqtt_command_topic": "roomba/stop",
                "description": "Stop all movement",
                "required_modes": ["safe", "full"],
                "recommended_mode_before_command": "safe",
                "parameters": {},
            },
            "drive": {
                "name": "drive",
                "mqtt_command_topic": "roomba/drive",
                "description": "Drive with velocity and radius",
                "required_modes": ["safe", "full"],
                "recommended_mode_before_command": "safe",
                "parameters": {
                    "velocity": {
                        "type": "int",
                        "required": True,
                        "description": "Velocity in mm/s",
                        "constraints": {
                            "minimum": -500,
                            "maximum": 500,
                            "unit": "mm/s",
                        },
                    },
                    "radius": {
                        "type": "int",
                        "required": True,
                        "description": "Radius in mm",
                        "constraints": {
                            "minimum": -2000,
                            "maximum": 2000,
                            "unit": "mm",
                            "allowed_special_values": [32767, 32768],
                            "special_values": {
                                "32767": "drive straight",
                                "32768": "drive straight",
                                "1": "turn in place counter-clockwise",
                                "-1": "turn in place clockwise",
                            },
                        },
                    },
                },
            },
            "drive_pwm": {
                "name": "drive_pwm",
                "mqtt_command_topic": "roomba/drive_pwm",
                "description": "Drive with PWM values for each wheel",
                "required_modes": ["safe", "full"],
                "recommended_mode_before_command": "safe",
                "parameters": {
                    "left_pwm": {
                        "type": "int",
                        "required": True,
                        "description": "Left wheel PWM",
                        "constraints": {
                            "minimum": -255,
                            "maximum": 255,
                        },
                    },
                    "right_pwm": {
                        "type": "int",
                        "required": True,
                        "description": "Right wheel PWM",
                        "constraints": {
                            "minimum": -255,
                            "maximum": 255,
                        },
                    },
                },
            },
            "motors": {
                "name": "motors",
                "mqtt_command_topic": "roomba/motors",
                "description": "Control motor PWM values",
                "required_modes": ["safe", "full"],
                "recommended_mode_before_command": "safe",
                "parameters": {
                    "left": {
                        "type": "int",
                        "required": True,
                        "description": "Left wheel velocity in mm/s",
                        "constraints": {
                            "minimum": -500,
                            "maximum": 500,
                            "unit": "mm/s",
                        },
                    },
                    "right": {
                        "type": "int",
                        "required": True,
                        "description": "Right wheel velocity in mm/s",
                        "constraints": {
                            "minimum": -500,
                            "maximum": 500,
                            "unit": "mm/s",
                        },
                    },
                },
            },
            "buttons": {
                "name": "buttons",
                "mqtt_command_topic": "roomba/buttons",
                "description": "Send button commands",
                "required_modes": ["passive", "safe", "full"],
                "parameters": {
                    "buttons": {
                        "type": "int",
                        "required": True,
                        "description": "Button bitmask",
                        "constraints": {
                            "minimum": 0,
                            "maximum": 255,
                            "unit": "byte",
                        },
                    }
                },
            },
            "song": {
                "name": "song",
                "mqtt_command_topic": "roomba/song",
                "description": "Define a song",
                "required_modes": ["safe", "full"],
                "constraints": {
                    "max_notes": 16,
                    "list_length_rule": "note_number_list and note_duration_list must have the same length between 1 and 16.",
                    "duration_unit": "1/64 second, not milliseconds",
                    "byte_rule": "Every song_number, note number, and duration value must be an integer in byte range 0-255 before being sent to the robot.",
                },
                "parameters": {
                    "song_number": {
                        "type": "int",
                        "required": True,
                        "description": "Song number",
                        "constraints": {
                            "minimum": 0,
                            "maximum": 4,
                        },
                    },
                    "note_number_list": {
                        "type": "array",
                        "required": True,
                        "description": "List of MIDI note numbers. Use 31-127 for audible notes; avoid 0 unless intentionally using a rest supported by the target Roomba model.",
                        "constraints": {
                            "min_items": 1,
                            "max_items": 16,
                            "item_type": "int",
                            "item_minimum": 31,
                            "item_maximum": 127,
                            "unit": "MIDI note number",
                        },
                    },
                    "note_duration_list": {
                        "type": "array",
                        "required": True,
                        "description": "List of note durations. Roomba durations are not milliseconds; each unit is 1/64 second.",
                        "constraints": {
                            "min_items": 1,
                            "max_items": 16,
                            "item_type": "int",
                            "item_minimum": 1,
                            "item_maximum": 255,
                            "unit": "1/64 second",
                            "examples": {
                                "8": "0.125 seconds",
                                "16": "0.25 seconds",
                                "32": "0.5 seconds",
                                "64": "1.0 second",
                            },
                        },
                    },
                },
            },
            "play_song": {
                "name": "play_song",
                "mqtt_command_topic": "roomba/play_song",
                "description": "Play a defined song",
                "required_modes": ["safe", "full"],
                "constraints": {
                    "precondition": "A song with the same song_number must have been defined first using the song command.",
                },
                "parameters": {
                    "song_number": {
                        "type": "int",
                        "required": True,
                        "description": "Song number to play",
                        "constraints": {
                            "minimum": 0,
                            "maximum": 4,
                        },
                    }
                },
            },
        }
