from setuptools import setup, find_packages

setup(
    name='sphinx-atl-topology',
    version='1.0.0',
    description='Sphinx extension for interactive ATL topology diagrams',
    author='Arista Training Labs',
    author_email='training@arista.com',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'sphinx_atl_topology': ['_static/*', '_static/images/*'],
    },
    install_requires=[
        'sphinx>=4.0',
        'pyyaml>=5.0',
    ],
    python_requires='>=3.8',
)
