from setuptools import setup, find_packages

setup(
    name='personal_brain',
    version='0.1.0',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click',
        'ollama',
        'pydantic',
        # 'sqlite-vec', # Optional, user might need to build or install specifically
        'pillow',
        # 'python-magic-bin; sys_platform == "win32"',
        # 'python-magic; sys_platform != "win32"',
        'requests',
        'tqdm',
        'tenacity'
    ],
    entry_points='''
        [console_scripts]
        pb=personal_brain.cli:cli
    ''',
)
