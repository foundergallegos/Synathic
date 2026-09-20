from setuptools import setup, find_packages

setup(
    name="synathic",
    version="0.1.1",
    packages=find_packages(),
    install_requires=["httpx>=0.24.0"],
    python_requires=">=3.9",
    long_description=open("README.md", encoding="utf-8").read(),
long_description_content_type="text/markdown",
)
