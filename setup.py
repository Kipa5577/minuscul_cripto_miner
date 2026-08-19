
"""
@author: pki
"""

from setuptools import find_packages, setup


with open("README.md", "r") as fh:
    long_description = fh.read()


setup(
    name="minuscul_crypto_miner",
    version= "2026.1.0",
    author='Patrick Kosa-Ivasca',
    description='Python-based cripto mier asic implementation',
    long_description=long_description,
    long_description_content_type="text/markdown",
    
    install_requires=[
        'py4hw>=2025.4',
        'pyelftools',
        'itanium-demangler'
    ],

    extras_require={
        'test':['pytest'],
    },
    packages=find_packages(),
    package_data={'': ['*.png','*.bin','*.hex']},
    classifiers =[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache License",
        "Operating System :: OS Independent",
        "Topic :: System :: Hardware",
    ],
    python_requires='>=3.7',
)