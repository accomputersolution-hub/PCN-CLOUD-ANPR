from setuptools import find_packages, setup

setup(
    name="pcn-anpr-engine",
    version="0.2.0",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "opencv-python-headless>=4.8.0,<5",
        "numpy>=1.24.0",
        "Pillow>=10.0.0",
    ],
    extras_require={
        "ocr": [
            "paddlepaddle==3.2.2",
            "paddleocr>=2.7.0,<3.8",
        ],
    },
)
