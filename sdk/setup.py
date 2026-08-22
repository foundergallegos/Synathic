from setuptools import setup, find_packages

setup(
    name="synathic",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["httpx>=0.24.0"],
    python_requires=">=3.9",
)