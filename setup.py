from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='web-traffic-bot',
    version='0.2.0',
    author='Lugayavu',
    description='Web traffic bot for personal website load testing and traffic simulation',
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "bot.dashboard": ["templates/*.html", "static/*"],
    },
    python_requires='>=3.8',
    install_requires=[
        'selenium>=4.0.0',
        'pyyaml>=5.4',
        'webdriver-manager>=3.8.0',
        'flask>=2.3.0',
    ],
    entry_points={
        'console_scripts': [
            'web-traffic-bot=bot.cli.__main__:main',
        ],
    },
    classifiers=[
        'Programming Language :: Python :: 3',
        'Operating System :: OS Independent',
    ],
)
