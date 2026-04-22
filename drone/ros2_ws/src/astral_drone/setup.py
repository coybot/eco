from setuptools import setup
import os
from glob import glob

package_name = 'astral_drone'

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
    maintainer='Astral Team',
    maintainer_email='team@astral.ai',
    description='Astral drone ROS2 integration',
    license='Proprietary',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'camera_node = astral_drone.camera_node:main',
            'mavlink_bridge = astral_drone.mavlink_bridge:main',
        ],
    },
)
