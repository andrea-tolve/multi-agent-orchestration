class Constant:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)


# Roomba Commands
ROOMBA_COMMANDS = Constant(
    START=128,
    BAUD=129,
    SAFE=131,
    FULL=132,
    POWER=133,
    SPOT=134,
    CLEAN=135,
    MAX=136,
    DRIVE=137,
    MOTORS=138,
    SONG=140,
    PLAY=141,
    SENSORS=142,
    SEEK_DOCK=143,
    PWM_MOTORS=144,
    DRIVE_DIRECT=145,
    DRIVE_PWM=146,
    STREAM=148,
    QUERY_LIST=149,
    BUTTONS=165,
)

# Roomba Sensor Packet IDs and Configuration
# Format: (Packet ID, Data Bytes, Signed)
ROOMBA_SENSORS = Constant(
    WHEEL_DROPS=(7, 1, False),
    WALL=(8, 1, False),
    CLIFF_LEFT=(9, 1, False),
    CLIFF_FRONT_LEFT=(10, 1, False),
    CLIFF_FRONT_RIGHT=(11, 1, False),
    CLIFF_RIGHT=(12, 1, False),
    VIRTUAL_WALL=(13, 1, False),
    WHEEL_OVERCURRENT=(14, 1, False),
    DIRT_DETECT=(15, 1, False),
    IFR_CHAR_OMNI=(17, 1, False),
    BUTTONS=(18, 1, False),
    DISTANCE=(19, 2, True),
    ANGLE=(20, 2, True),
    CHARGING_STATE=(21, 1, False),
    VOLTAGE=(22, 2, False),
    CURRENT=(23, 2, True),
    TEMPERATURE=(24, 1, True),
    BATTERY_CHARGE=(25, 2, False),
    BATTERY_CAPACITY=(26, 2, False),
    CLIFF_LEFT_SIGNAL=(28, 2, False),
    CLIFF_FRONT_LEFT_SIGNAL=(29, 2, False),
    CLIFF_FRONT_RIGHT_SIGNAL=(30, 2, False),
    CLIFF_RIGHT_SIGNAL=(31, 2, False),
    CHARGING_SOURCES_AVAILABLE=(34, 1, False),
    OI_MODE=(35, 1, False),
    SONG_NUMBER=(36, 1, False),
    SONG_PLAYING=(37, 1, False),
    NUMBER_OF_STREAM_PACKETS=(38, 1, False),
    REQUESTED_VELOCITY=(39, 2, True),
    REQUESTED_RADIUS=(40, 2, True),
    REQUESTED_RIGHT_VELOCITY=(41, 2, True),
    REQUESTED_LEFT_VELOCITY=(42, 2, True),
    LEFT_ENCODER_COUNTS=(43, 2, True),
    RIGHT_ENCODER_COUNTS=(44, 2, True),
    LIGHT_BUMPER=(45, 1, False),
    LIGHT_BUMP_LEFT_SIGNAL=(46, 2, False),
    LIGHT_BUMP_FRONT_LEFT_SIGNAL=(47, 2, False),
    LIGHT_BUMP_FRONT_CENTER_LEFT_SIGNAL=(48, 2, False),
    LIGHT_BUMP_FRONT_CENTER_RIGHT_SIGNAL=(49, 2, False),
    LIGHT_BUMP_FRONT_RIGHT_SIGNAL=(50, 2, False),
    LIGHT_BUMP_RIGHT_SIGNAL=(51, 2, False),
    IFR_CHAR_LEFT=(52, 1, False),
    IFR_CHAR_RIGHT=(53, 1, False),
    LEFT_MOTOR_CURRENT=(54, 2, True),
    RIGHT_MOTOR_CURRENT=(55, 2, True),
    MAIN_BRUSH_MOTOR_CURRENT=(56, 2, True),
    SIDE_BRUSH_MOTOR_CURRENT=(57, 2, True),
    STASIS=(58, 1, False),
)

# Roomba Movement Parameters
ROOMBA_PARAMS = Constant(
    STRAIGHT_RADIUS=32768,
    MIN_RADIUS=-2000,
    MAX_RADIUS=2000,
    MIN_VELOCITY=-500,
    MAX_VELOCITY=500,
)

# Roomba Physical Constants
ROOMBA_PHYSICAL = Constant(
    DEFAULT_WHEEL_SPAN_MM=235.0,
    DEFAULT_BAUD_RATE=115200,
    DEFAULT_TIMEOUT_SEC=1.0,
)

# Roomba OI Modes
ROOMBA_OI_MODES = Constant(
    OFF=0,
    PASSIVE=1,
    SAFE=2,
    FULL=3,
)

# Roomba Charging States
ROOMBA_CHARGING_STATES = Constant(
    NOT_CHARGING=0,
    RECONDITIONING=1,
    FULL_CHARGING=2,
    TRICKLE_CHARGING=3,
    WAITING=4,
    CHARGING_FAULT=5,
)
