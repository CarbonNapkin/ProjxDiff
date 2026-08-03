"""
Projx Diff

A tool to compare two versions of a DriveWorks project and generate an HTML
report showing all differences in variables, constants, forms, macros,
calculation tables, lookup tables, and more.
"""

from ._version import __version__, __author__, __url__, __license__
from .models import (
    Variable,
    Constant,
    CalcTable,
    ComponentTask,
    DWProject,
    SpecMacro,
    SpecMacroTask,
    NavStep,
    DataTableDef,
    Form,
    FormControl,
)
from .parsers import load_project
from .report import generate_html_report
from .jsondiff import build_diff

__all__ = [
    'Variable',
    'Constant',
    'CalcTable',
    'ComponentTask',
    'DWProject',
    'SpecMacro',
    'SpecMacroTask',
    'NavStep',
    'DataTableDef',
    'Form',
    'FormControl',
    'load_project',
    'generate_html_report',
    'build_diff',
    '__version__',
    '__author__',
    '__url__',
    '__license__',
]
