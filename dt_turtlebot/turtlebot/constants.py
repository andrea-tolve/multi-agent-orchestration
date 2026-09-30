class Constant:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)


TURTLEBOT_MODEL = Constant(BURGER="burger")

# Robot Physical Constants
ROBOT = Constant(
    BURGER=Constant(
        WHEEL_DIAMETER=0.066,  # meters
        WHEEL_SEPARATION=0.160,  # meters between wheels
        TICK_PER_REV=4096,  # encoder ticks per revolution
        MAX_LINEAR_VEL=0.22,  # m/s
        MAX_ANGULAR_VEL=2.84,  # rad/s
        MAX_BATTERY_VOLTAGE=12.0,
        MIN_BATTERY_VOLTAGE=9.5,
    )
)

# ROS2 Topics
ROS2_TOPICS = Constant(
    # Sensor topics
    ODOM="/odom",
    IMU="/imu",
    SCAN="/scan",
    BATTERY="/battery_state",
    # Command topics
    CMD_VEL="/cmd_vel",
    # Info topics
    JOINT_STATES="/joint_states",
    TF="/tf",
    TF_STATIC="/tf_static",
)
