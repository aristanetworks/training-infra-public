# Implementation Plan: Configlet Builder Source Collection

## Overview
Extend `upload_exam_unattended.py` to collect configlet builder source code in addition to the generated configlet content currently being collected.

## Current State Analysis

### What's Currently Collected
- **Static configlets**: Full configuration text via `get_configlets_for_device()` (line 290-351)
- **Configlet metadata**: name, key, type, user, reconciled, dateTimeInLongFormat, netElementId
- **Builder awareness**: Script displays `isAutoBuilder` field (lines 1917, 2231) but doesn't collect builder source

### What's Missing
- **Builder source code** (mainScript - Python/Jinja2 template)
- **Form definitions** (formList - input form schema)
- **Form data** (actual form values filled in by user)
- **Builder metadata** (additional builder-specific fields)

## CVP API Investigation Required

### API Methods to Research
1. **`get_configlet_by_name()`** - Currently used, check if it returns builder fields
2. **`get_configlet_builder()`** or similar - May exist in cvprac library
3. **REST API endpoints**:
   - `/cvpservice/configlet/getConfigletBuilder.do?configletBuilderId=<id>`
   - `/cvpservice/configlet/getConfigletBuilders.do`
   - `/cvpservice/configlet/getConfigletByName.do?name=<name>`

### Fields to Identify in API Response
According to CVP API documentation, configlet builders contain:
- `name` - Builder name
- `key` - Unique identifier
- `mainScript` - Python/Jinja2 source code
- `formList` - Form field definitions (JSON array)
- `data` - Form data values
- `isDraft` - Whether builder is in draft state
- `isAutoBuilder` - Boolean flag (already collected)
- `type` - Should be "Builder" for configlet builders

## Implementation Plan

### Phase 1: Research & Validation (1-2 hours)
**Objective**: Verify CVP API methods and response structure

1. **Test CVP API Methods**
   - Check if `client.api.get_configlet_by_name()` already returns `mainScript` and `formList`
   - Test alternative methods in cvprac library
   - Document actual API response structure

2. **Identify Builder Configlets**
   - Review exam topologies for examples of configlet builders
   - Find test cases with known builders
   - Document expected output format

### Phase 2: Code Implementation (2-3 hours)
**Objective**: Add builder source collection to the script

#### 2.1: Create New Method - `get_configlet_builder_source()`

**Location**: Add after `get_configlets_for_device()` method (around line 352)

**Method signature**:
```python
def get_configlet_builder_source(self, configlet_name, configlet_key):
    """
    Get configlet builder source code and form definition

    Args:
        configlet_name (str): Name of the configlet builder
        configlet_key (str): Unique key for the configlet

    Returns:
        dict: Builder source data with keys:
            - mainScript (str): Python/Jinja2 source code
            - formList (list): Form field definitions
            - data (dict): Form data values
            - isDraft (bool): Draft status
            - additional metadata

        Returns None if not a builder or error occurs
    """
```

**Implementation approach**:
1. Try using existing cvprac methods first:
   ```python
   # Option 1: Check if get_configlet_by_name returns all fields
   configlet_full = self.client.api.get_configlet_by_name(configlet_name)
   if 'mainScript' in configlet_full:
       return configlet_full
   ```

2. If not available, use direct REST API call:
   ```python
   # Option 2: Direct API call
   try:
       url = f'/cvpservice/configlet/getConfigletBuilder.do?configletBuilderId={configlet_key}'
       response = self.client.get(url)
       return response
   except Exception as e:
       print(f"  Note: Could not retrieve builder source: {e}")
       return None
   ```

#### 2.2: Modify `save_configlets_to_files()` Method

**Location**: Lines 1374-1410

**Changes**:
1. Add builder source handling after saving static configlet:
   ```python
   # After line 1405 (after saving config_file)

   # If this is a builder, save the source code
   if configlet.get('isAutoBuilder') or configlet.get('type') == 'Builder':
       builder_source = configlet.get('builder_source')
       if builder_source:
           # Save main script (Python/Jinja2 source)
           if 'mainScript' in builder_source:
               script_file = os.path.join(configlets_dir, f'{safe_name}_mainScript.py')
               with open(script_file, 'w') as f:
                   f.write(builder_source['mainScript'])
               saved_files.append(script_file)

           # Save form definition
           if 'formList' in builder_source:
               form_file = os.path.join(configlets_dir, f'{safe_name}_formList.json')
               with open(form_file, 'w') as f:
                   json.dump(builder_source['formList'], f, indent=2)
               saved_files.append(form_file)

           # Save form data
           if 'data' in builder_source:
               data_file = os.path.join(configlets_dir, f'{safe_name}_formData.json')
               with open(data_file, 'w') as f:
                   json.dump(builder_source['data'], f, indent=2)
               saved_files.append(data_file)
   ```

#### 2.3: Enhance `get_configlets_for_device()` Method

**Location**: Lines 290-351

**Changes**:
Add builder source fetching in the configlet loop:

```python
# After line 349 (in the loop processing valid_configlets)
for item in configlets:
    if isinstance(item, dict):
        # Existing code...
        valid_configlets.append(item)

        # NEW: Fetch builder source if this is a builder
        if item.get('isAutoBuilder') or item.get('type') == 'Builder':
            configlet_name = item.get('name')
            configlet_key = item.get('key')
            if configlet_name and configlet_key:
                builder_source = self.get_configlet_builder_source(configlet_name, configlet_key)
                if builder_source:
                    # Add builder source to the configlet dict
                    item['builder_source'] = builder_source
                    print(f"    ✓ Retrieved builder source for '{configlet_name}'")
```

#### 2.4: Update Report Formatting Methods

**Locations**:
- `format_device_report()` - lines 1852-2006
- `format_output()` - lines 2008-2594

**Changes for both methods** (around lines 1920-1940 and 2220-2260):

After displaying "Is Auto Builder" field, add:

```python
# After line 1917 (or 2231)
output.append(f"Is Auto Builder: {configlet.get('isAutoBuilder', 'N/A')}")

# NEW: Show builder source information
if configlet.get('isAutoBuilder') or configlet.get('type') == 'Builder':
    builder_source = configlet.get('builder_source')
    if builder_source:
        output.append("\nConfiglet Builder Source:")
        output.append("  " + "-" * 90)

        # Show main script preview
        if 'mainScript' in builder_source:
            script_lines = builder_source['mainScript'].split('\n')
            output.append(f"  Main Script Lines: {len(script_lines)}")
            output.append(f"  Script Preview (first 10 lines):")
            for line in script_lines[:10]:
                output.append(f"    {line}")
            if len(script_lines) > 10:
                output.append(f"    ... ({len(script_lines) - 10} more lines)")

        # Show form fields
        if 'formList' in builder_source:
            form_list = builder_source['formList']
            output.append(f"\n  Form Fields: {len(form_list)} field(s)")
            for idx, field in enumerate(form_list[:5], 1):
                field_name = field.get('fieldId', 'unknown')
                field_type = field.get('type', 'unknown')
                output.append(f"    [{idx}] {field_name} ({field_type})")
            if len(form_list) > 5:
                output.append(f"    ... and {len(form_list) - 5} more")

        # Show form data values
        if 'data' in builder_source and builder_source['data']:
            output.append(f"\n  Form Data Values:")
            data = builder_source['data']
            if isinstance(data, dict):
                for key, value in list(data.items())[:5]:
                    output.append(f"    {key}: {value}")
                if len(data) > 5:
                    output.append(f"    ... and {len(data) - 5} more")

        output.append("  " + "-" * 90)

        if output_dir:
            output.append(f"  Builder source saved to: configlets/{safe_name}_mainScript.py")
            output.append(f"  Form definition saved to: configlets/{safe_name}_formList.json")
            if builder_source.get('data'):
                output.append(f"  Form data saved to: configlets/{safe_name}_formData.json")
    else:
        output.append("\nConfiglet Builder: Source not available")
```

### Phase 3: Testing (1-2 hours)
**Objective**: Verify builder source collection works correctly

1. **Unit Testing**
   - Test with known builder configlets
   - Test with static configlets (should not break existing functionality)
   - Test with missing/unavailable builders

2. **Integration Testing**
   - Run full script on test topology with builders
   - Verify file structure matches expected output
   - Validate JSON/Python syntax in saved files

3. **Edge Cases**
   - Empty formList
   - No form data
   - Builder in draft state
   - Builder with syntax errors

### Phase 4: Documentation (30 minutes)
**Objective**: Update documentation and code comments

1. **Update Method Docstrings**
   - Document new `get_configlet_builder_source()` method
   - Update `get_configlets_for_device()` docstring
   - Update `save_configlets_to_files()` docstring

2. **Update Output Structure Documentation**
   - Add builder files to output tree (lines 2838-2856)
   ```
   │   ├── configlets/
   │   │   ├── *_config.txt          # Generated configuration
   │   │   ├── *_metadata.json       # Configlet metadata
   │   │   ├── *_mainScript.py       # Builder source (if builder)
   │   │   ├── *_formList.json       # Builder form definition (if builder)
   │   │   └── *_formData.json       # Builder form values (if builder)
   ```

3. **Add Comments**
   - Comment explaining builder vs static configlet handling
   - Reference CVP API documentation

## Testing Strategy

### Test Scenarios

1. **Scenario 1: Static Configlet Only**
   - Input: Device with only static configlets
   - Expected: No builder files created, existing behavior preserved

2. **Scenario 2: Builder Configlet with Full Data**
   - Input: Device with configlet builder (mainScript, formList, data populated)
   - Expected: All builder files created with correct content

3. **Scenario 3: Builder with No Form Data**
   - Input: Builder with mainScript but no data
   - Expected: mainScript and formList saved, no formData file

4. **Scenario 4: Mixed Configlets**
   - Input: Device with both static and builder configlets
   - Expected: Correct files for each type

5. **Scenario 5: API Error Handling**
   - Input: Builder API returns error
   - Expected: Graceful degradation, log warning, continue processing

### Validation Checklist

- [ ] mainScript files contain valid Python/Jinja2 syntax
- [ ] formList JSON is valid and properly formatted
- [ ] formData JSON is valid and properly formatted
- [ ] Metadata JSON includes builder_source key when appropriate
- [ ] Console output shows builder source collection progress
- [ ] Reports display builder information correctly
- [ ] No regression in static configlet handling
- [ ] Error messages are helpful and informative

## File Structure Changes

### Before (Current)
```
cvp_exam_data/
├── Overview_report.txt
├── device1/
│   ├── device1_report.txt
│   ├── raw_data.json
│   ├── configlets/
│   │   ├── ConfigletA_config.txt        # Generated config
│   │   ├── ConfigletA_metadata.json     # Metadata only
│   │   ├── ConfigletB_config.txt
│   │   └── ConfigletB_metadata.json
│   └── eapi_data/
```

### After (With Builder Support)
```
cvp_exam_data/
├── Overview_report.txt
├── device1/
│   ├── device1_report.txt
│   ├── raw_data.json
│   ├── configlets/
│   │   ├── ConfigletA_config.txt          # Generated config (static)
│   │   ├── ConfigletA_metadata.json       # Metadata
│   │   ├── BuilderB_config.txt            # Generated config (from builder)
│   │   ├── BuilderB_metadata.json         # Metadata (includes builder_source)
│   │   ├── BuilderB_mainScript.py         # ** NEW ** Python/Jinja2 source
│   │   ├── BuilderB_formList.json         # ** NEW ** Form definition
│   │   └── BuilderB_formData.json         # ** NEW ** Form values
│   └── eapi_data/
```

## Dependencies

### Required Libraries
- cvprac (already installed)
- requests (already installed)
- json (stdlib)
- os (stdlib)

### CVP Version Requirements
- CVP 2018.2+ (configlet builder feature)
- API version compatibility check may be needed

## Risk Assessment

### Low Risk
- Additive changes only (no modification to existing static configlet logic)
- Error handling prevents script failure
- Backward compatible with CVP versions without builders

### Medium Risk
- API method availability varies by CVP version
- Builder source may contain sensitive data (passwords, tokens)
  - **Mitigation**: Document in user guide, add warning in output

### High Risk
- None identified

## Success Criteria

1. ✅ Script successfully identifies configlet builders via `isAutoBuilder` field
2. ✅ Builder source code (mainScript) is retrieved and saved
3. ✅ Form definitions (formList) are retrieved and saved
4. ✅ Form data values are retrieved and saved (when present)
5. ✅ Static configlet handling remains unchanged
6. ✅ Reports display builder information clearly
7. ✅ Error handling prevents script crashes
8. ✅ Documentation is updated with new file structure

## Timeline Estimate

- **Research & API validation**: 1-2 hours
- **Implementation**: 2-3 hours
- **Testing**: 1-2 hours
- **Documentation**: 30 minutes
- **Total**: 5-8 hours

## Future Enhancements (Out of Scope)

1. Builder version history collection
2. Builder dependencies analysis
3. Syntax validation of mainScript
4. Form data diff against previous submissions
5. Builder execution simulation
6. Security scanning of builder scripts

## Notes

- Configlet builders are a CVP feature for dynamic configuration generation
- Builder source code may be intellectual property - handle carefully
- Some exam scenarios may use builders to test student's understanding
- Collecting builder source is essential for grading automation and plagiarism detection
