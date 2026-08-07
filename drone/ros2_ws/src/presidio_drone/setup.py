from setuptools import setup
import os
from glob import glob

package_name = 'presidio_drone'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Presidio Team',
    maintainer_email='team@astral.ai',
    description='Presidio drone ROS2 integration',
    license='Proprietary',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'camera_node = presidio_drone.camera_node:main',
            'mavlink_bridge = presidio_drone.mavlink_bridge:main',
        ],
    },
)
