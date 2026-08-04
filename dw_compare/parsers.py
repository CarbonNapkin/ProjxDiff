"""
XML parsers for DriveWorks project files.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from .models import (
    Variable, Constant, CalcTable, ComponentTask, DWProject,
    SpecMacro, SpecMacroTask, NavStep, DataTableDef,
    Form, FormControl,
)


# Namespace mappings for DriveWorks XML
NS = {
    'project': 'http://schemas.driveworks.co.uk/project/',
    'comp-task': 'http://schemas.driveworks.co.uk/p-component-task/',
    'pcomp': 'http://schemas.driveworks.co.uk/p-component/',
    'meta': 'http://schemas.driveworks.co.uk/project-metadata/',
}


def _local_name(el: ET.Element) -> str:
    """Tag with any XML namespace stripped."""
    return el.tag.rsplit('}', 1)[-1]


def _read_form_property(el: ET.Element) -> tuple:
    """Each Form/Control property element looks like
        <Foo IsStatic="True"><Value>X</Value></Foo>
    or
        <Foo IsStatic="False"><Rule>=formula</Rule></Foo>
    or
        <Foo IsStatic="False"><Value>resolved-value</Value></Foo>
    Returns (is_static, formula_or_value). <Rule> wins over <Value> when both
    are present.
    """
    is_static = (el.get('IsStatic', 'True').lower() != 'false')
    rule = None
    value = ''
    for child in el:
        ln = _local_name(child)
        if ln == 'Rule':
            rule = (child.text or '').strip()
            break
        if ln == 'Value':
            value = (child.text or '').strip()
    return is_static, (rule if rule is not None else value)


def _strip_assembly(type_str: str) -> str:
    """Strip the .NET assembly qualifier from a type string like
    'DriveWorks.Email, DriveWorks.Engine' -> 'DriveWorks.Email', and then
    return the final type segment ('Email'). Empty input -> empty string.
    """
    type_only = (type_str or '').split(',', 1)[0].strip()
    return type_only.rsplit('.', 1)[-1] if type_only else ''


def _parse_form(form_el: ET.Element) -> Form:
    """Build a Form from a <Form Name="..."> element. Form-level rule props
    are direct children, while <Controls> wraps individual controls (one
    element per control, tag name = control type, e.g. ComboBox)."""
    name = form_el.get('Name', '')
    form_props = {}
    controls = {}
    for child in form_el:
        ln = _local_name(child)
        if ln == 'Controls':
            for ctrl_el in child:
                ctrl_name = ctrl_el.get('Name', '')
                if not ctrl_name:
                    continue
                ctrl_type = _local_name(ctrl_el)
                props = {}
                for prop_el in ctrl_el:
                    props[_local_name(prop_el)] = _read_form_property(prop_el)
                controls[ctrl_name] = FormControl(
                    name=ctrl_name, control_type=ctrl_type, props=props
                )
        else:
            form_props[ln] = _read_form_property(child)
    return Form(name=name, form_props=form_props, controls=controls)


def parse_project_xml(path: Path) -> dict:
    """Parse project.xml for variables, calc tables, documents, data tables,
    specification macros, variable categories, and forms."""
    data = {
        'variables': {},
        'calc_tables': {},
        'documents': {},
        'data_tables': {},
        'spec_macros': {},
        'categories': {},
        'forms': {},
    }

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as e:
        print(f"Warning: Could not parse {path}: {e}")
        return data
    
    # Parse Variables - try both namespaced (old) and attribute-based (TDM) formats
    for var in root.findall('.//project:Variable', NS):
        name = var.get('Name', '')
        formula = ""
        comment = ""
        
        formula_el = var.find('project:Formula', NS)
        if formula_el is not None and formula_el.text:
            formula = formula_el.text.strip()
        
        comment_el = var.find('project:Comment', NS)
        if comment_el is not None and comment_el.text:
            comment = comment_el.text.strip()
        
        if name:
            data['variables'][name] = Variable(name=name, formula=formula, comment=comment)
    
    # Also try non-namespaced Variable elements (TDM format)
    for var in root.findall('.//Variable'):
        display_name = var.get('DisplayName', '')
        store_name = var.get('StoreName', '')
        rule = var.get('Rule', '')
        comment = var.get('Comment', '')
        category = var.get('Category', '')
        
        if display_name:
            data['variables'][display_name] = Variable(
                name=display_name, store_name=store_name, 
                formula=rule, comment=comment, category=category
            )
    
    # Parse Calculation Tables
    for table in root.findall('.//project:CalculationTable', NS):
        tbl_name = table.get('Name', '')
        try:
            row_count = int(table.get('RowCount') or 0)
        except (TypeError, ValueError):
            row_count = 0
        
        columns = {}
        for col in table.findall('project:Columns/project:Column', NS):
            col_name = col.get('Name', '')
            col_data = {'common': '', 'rows': {}}
            
            # Common rule
            common = col.find('project:CommonRule/project:Formula', NS)
            if common is not None and common.text:
                col_data['common'] = common.text.strip()
            
            # Row-specific rules
            for rule in col.findall('project:Rules/project:Rule', NS):
                row_idx = rule.get('RowIndex')
                formula_el = rule.find('project:Formula', NS)
                if row_idx is not None and formula_el is not None:
                    try:
                        col_data['rows'][int(row_idx)] = (formula_el.text or '').strip()
                    except (TypeError, ValueError):
                        pass  # ignore a malformed (non-numeric) RowIndex
            
            columns[col_name] = col_data
        
        if tbl_name:
            data['calc_tables'][tbl_name] = CalcTable(name=tbl_name, row_count=row_count, columns=columns)
    
    # Parse Documents (Triggered Actions, etc.)
    for doc in root.findall('.//project:Document', NS):
        doc_name = doc.get('Name', '')
        doc_type = _strip_assembly(doc.get('Type', ''))
        rules = {}

        for rule in doc.findall('.//project:Rule', NS):
            rule_id = rule.get('Id', '')
            formula_el = rule.find('project:Formula', NS)
            if rule_id and formula_el is not None:
                rules[rule_id] = (formula_el.text or '').strip()

        if doc_name:
            data['documents'][doc_name] = {'type': doc_type, 'rules': rules}

    # Parse Variable Categories (GUID -> human name)
    for cat in root.findall('.//project:Category', NS):
        cat_id = cat.get('UniqueId', '')
        cat_name = cat.get('Name', '')
        if cat_id and cat_name:
            data['categories'][cat_id] = cat_name

    # Parse Data Tables (name + type, the row data lives in CSV elsewhere)
    for dt in root.findall('.//project:DataTable', NS):
        dt_name = dt.get('Name', '')
        dt_type = _strip_assembly(dt.get('Type', ''))
        if dt_name:
            data['data_tables'][dt_name] = DataTableDef(name=dt_name, table_type=dt_type)

    # Parse Specification Macros. Inner elements live under several namespaces
    # (specification-flow, event-flow, etc.) so match by local name throughout.
    for macro in root.findall('.//project:SpecificationMacro', NS):
        macro_name = macro.get('Name', '')
        if not macro_name:
            continue
        tasks = []
        for tasks_block in macro:
            if _local_name(tasks_block) != 'Tasks':
                continue
            for task_el in tasks_block:
                if _local_name(task_el) != 'Task':
                    continue
                task = SpecMacroTask(
                    title=task_el.get('Title', ''),
                    task_type=_strip_assembly(task_el.get('Type', '')),
                )
                # Pull each Property -> Binding -> Formula formula into a dict.
                for child in task_el:
                    if _local_name(child) != 'Properties':
                        continue
                    for prop in child:
                        if _local_name(prop) != 'Property':
                            continue
                        prop_name = prop.get('Name', '')
                        if not prop_name:
                            continue
                        formula_text = ''
                        for binding in prop:
                            if _local_name(binding) != 'Binding':
                                continue
                            for ff in binding:
                                if _local_name(ff) == 'Formula' and ff.text:
                                    formula_text = ff.text.strip()
                                    break
                            if formula_text:
                                break
                        task.properties[prop_name] = formula_text
                tasks.append(task)
        data['spec_macros'][macro_name] = SpecMacro(name=macro_name, tasks=tasks)

    # Parse Forms. The <Forms> container lives in the project namespace, but
    # each <Form> element and its descendants live in
    # "pa-namespace:DriveWorks.Forms,DriveWorks.Engine". Match by local name
    # to avoid having to register that unusual namespace URI.
    forms_container = root.find('project:Forms', NS)
    if forms_container is not None:
        for form_el in forms_container:
            if _local_name(form_el) != 'Form':
                continue
            form = _parse_form(form_el)
            if form.name:
                data['forms'][form.name] = form

    return data


def parse_design_master(path: Path) -> dict:
    """Parse designMaster.xml (or TDM format) for constants, special vars, lookup tables, variables, and navigation steps."""
    data = {
        'constants': {},
        'lookup_tables': {},
        'variables': {},
        'nav_steps': {},
    }

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as e:
        print(f"Warning: Could not parse {path}: {e}")
        return data
    
    # Parse Constants - attribute-based format
    for const in root.findall('.//Constant'):
        display_name = const.get('DisplayName', '')
        store = const.get('StoreName', '')
        value = const.get('Value', '')
        comment = const.get('Comment', '')
        
        if display_name:
            data['constants'][display_name] = Constant(
                name=display_name, store_name=store, value=value, comment=comment
            )
    
    # Parse Variables from TDM format (attribute-based)
    for var in root.findall('.//Variable'):
        display_name = var.get('DisplayName', '')
        store_name = var.get('StoreName', '')
        rule = var.get('Rule', '')
        comment = var.get('Comment', '')
        category = var.get('Category', '')
        
        if display_name:
            data['variables'][display_name] = Variable(
                name=display_name, store_name=store_name,
                formula=rule, comment=comment, category=category
            )
    
    # Parse Lookup Tables
    for table in root.findall('.//Table'):
        name = table.get('Name', '')
        content = table.text or ''
        if name:
            data['lookup_tables'][name] = content.strip()

    # Parse Navigation Steps (attribute-only elements inside <Navigation>)
    for step in root.findall('.//Navigation/Step'):
        step_name = step.get('Name', '')
        if not step_name:
            continue
        data['nav_steps'][step_name] = NavStep(
            name=step_name,
            step_type=step.get('Type', ''),
            next_step_rule=step.get('NextStepRule', ''),
            next_step_value=step.get('NextStepValue', ''),
            next_macro_value=step.get('NextMacroValue', ''),
            previous_macro_value=step.get('PreviousMacroValue', ''),
        )

    return data


def parse_component_tasks(path: Path) -> dict:
    """Parse componentTasks.xml"""
    tasks = {}

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as e:
        print(f"Warning: Could not parse {path}: {e}")
        return tasks

    # .//comp-task:Task picks up Tasks under both <ComponentSpecific> and
    # <TypeSpecific> wrappers because the search is recursive.
    for task in root.findall('.//comp-task:Task', NS):
        task_id = task.get('Id', '')
        name = task.get('Name', '')
        task_type = _strip_assembly(task.get('Type', ''))
        comp_id = task.get('ComponentId', '')
        scope = task.get('Scope', '')

        # Rules may live under any namespace inside the task — match by local name.
        rules = {}
        for rule in task.iter():
            if _local_name(rule) != 'Rule':
                continue
            rule_name = rule.get('Name', '')
            if not rule_name:
                continue
            formula_text = None
            for child in rule:
                if _local_name(child) == 'Formula':
                    formula_text = (child.text or '').strip()
                    break
            if formula_text is not None:
                rules[rule_name] = formula_text

        base_key = f"{name}|{comp_id or scope or task_id}"
        key = base_key
        dup = 2
        while key in tasks:
            key = f"{base_key}#{dup}"
            dup += 1
        tasks[key] = ComponentTask(
            id=task_id, name=name, task_type=task_type,
            component_id=comp_id, scope=scope, rules=rules
        )

    return tasks


def load_project(folder: Path) -> DWProject:
    """Load all DriveWorks project files from a folder (recursively)"""
    proj = DWProject()

    project_files = sorted(folder.rglob('project.xml'))
    design_files = sorted(folder.rglob('designMaster.xml')) + sorted(folder.rglob('*.tdm'))
    task_files = sorted(folder.rglob('componentTasks.xml'))

    # A standard project has one project.xml and one design master. More than
    # one usually means embedded/nested specifications, whose contents are
    # merged into a single flat view here. Surface that, since same-named items
    # across specifications overwrite each other on merge.
    if len(project_files) > 1 or len(design_files) > 1:
        print(f"  ⚠ Multiple specifications detected ({len(project_files)} project.xml, "
              f"{len(design_files)} design master(s)). Their contents are merged into one "
              f"view; identically named items across specifications may overwrite each other.")

    for proj_file in project_files:
        print(f"  Found: {proj_file}")
        data = parse_project_xml(proj_file)
        proj.variables.update(data['variables'])
        proj.calc_tables.update(data['calc_tables'])
        proj.documents.update(data['documents'])
        proj.data_tables.update(data.get('data_tables', {}))
        proj.spec_macros.update(data.get('spec_macros', {}))
        proj.categories.update(data.get('categories', {}))
        proj.forms.update(data.get('forms', {}))

    for dm_file in design_files:
        print(f"  Found: {dm_file}")
        data = parse_design_master(dm_file)
        proj.constants.update(data['constants'])
        proj.lookup_tables.update(data['lookup_tables'])
        proj.variables.update(data.get('variables', {}))
        proj.nav_steps.update(data.get('nav_steps', {}))

    for ct_file in task_files:
        print(f"  Found: {ct_file}")
        proj.component_tasks.update(parse_component_tasks(ct_file))

    # Component Sets, placed components, and driven-property rules — all
    # free, no database needed, straight from project.xml and components/.
    from . import components as _components
    proj.component_index = _components.build_component_index(str(folder))

    # Resolve category GUIDs on Variables to human-readable names. Leave the
    # raw GUID in place if the project never declared a matching Category.
    if proj.categories:
        for v in proj.variables.values():
            if v.category and v.category in proj.categories:
                v.category = proj.categories[v.category]
    
    return proj