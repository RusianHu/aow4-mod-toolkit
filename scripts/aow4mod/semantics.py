"""Small, evidence-bound schemas. Never apply defaults across unverified builds."""
from pathlib import Path

from .core import FormatError, describe, sha

RESOURCE_ED_SHA = "60d53fd3852ae26fcb9822d78692d788b0334971582e85a776090e4af44efe88"
KNOWN_FIELDS = {
    "0x00471C6D/0x01": {"name": "FriendlyFireType", "codec": "u32", "default": 2,
                         "enum": {0: "Enemies Only", 1: "Friendlies Only", 2: "Everything",
                                  3: "Own Stuff Only", 4: "Not Own Stuff Only", 5: "Nothing",
                                  6: "Independents Only", 7: "Players Only", 8: "War Only", 9: "Not War Only"},
                         "evidence": "ResourceED serializer 0x1F432C0, constructor 0x1F233A7, enum table 0x2FFAB10"},
    "0x00473702/0x11": {"name": "Research AI priority link", "codec": "resource_link",
                         "evidence": "Current original and Mod link identity comparison; no default inferred"},
}

MANTA_RESOURCE_ED_SHA = '2d4a1bcacc99a25fd6e71efb94de3daa99c766348897c8424618a363ae903015'
# Storage extraction and curated gameplay roles have separate review claims.
# A future field-only profile must not silently enable the gameplay rules.
PROFILES = {
    RESOURCE_ED_SHA: {'name':'witch-1.1', 'fields':KNOWN_FIELDS, 'gameplay_ruleset':'witch-1.1-gameplay/2'},
    MANTA_RESOURCE_ED_SHA: {
        'name':'manta-26274772',
        'fields':{key:{**field,'evidence':'Manta generated serializer/corpus and current TMagicFriendlyFireType registry; reviewed resource identity for links'}
                  for key,field in KNOWN_FIELDS.items()},
        'gameplay_ruleset':'manta-26274772-gameplay/1',
    },
    'd9e2530a18a8665b67c3d0a941dffd071343880b7430617acb0262afa1f47e0e': {
        'name':'manta-26440582',
        'fields':{key:{**field,'evidence':'Build 26440582 serializer/corpus and enum re-audit; no missing-field rules inherited without comparison'}
                  for key,field in KNOWN_FIELDS.items()},
        'gameplay_ruleset':'manta-26440582-gameplay/1',
    },
    '16e19fa50154c243f92333e795ae2f789a4c952588aa42cafc911d48af82f896': {
        'name':'manta-26482553',
        'fields':{key:{**field,'evidence':'Build 26482553 full serializer declarations/layout and enum re-audit against 26440582; current corpus widths checked'}
                  for key,field in KNOWN_FIELDS.items()},
        'gameplay_ruleset':'manta-26482553-gameplay/1',
    },
}


def schema_status(game):
    exe = Path(game) / "ResourceED.exe" if game else None
    observed = sha(exe.read_bytes()) if exe and exe.is_file() else None
    profile=PROFILES.get(observed,{})
    return {"verified": observed in PROFILES, "observed_tool_sha256": observed,
            "supported_tool_sha256": observed if observed in PROFILES else RESOURCE_ED_SHA,
            "supported_tool_sha256s":list(PROFILES),
            "profile":profile.get('name'),
            "gameplay_rules_verified":bool(profile.get('gameplay_ruleset')),
            "gameplay_ruleset":profile.get('gameplay_ruleset'),
            "coverage": "Two evidenced property labels; one enum/default. Not a general reflection schema."}


def schema_bundle(game,schema_path=None):
    """Curated semantics override extracted storage facts; never infer signedness."""
    status=schema_status(game);fields={};types={}
    path=Path(schema_path) if schema_path else Path('.aow4mod/schema.json')
    status['extracted_verified']=False
    if path.is_file():
        from .reflection import load
        try:
            data=load(path,status['observed_tool_sha256']);types=data['types']
            status.update(extracted_verified=True,schema_path=str(path.resolve()),
                          schema_sha256=sha(path.read_bytes()),extracted_summary=data['summary'])
            status['coverage']=data['coverage']
            for key,field in data['fields'].items():
                item={k:field[k] for k in ('name','storage','width','default_hex','storage_validated','confidence','observed')}
                item['evidence']='build-bound generated serializer and observed original resources'
                if field['storage_validated']:
                    if field['storage']=='bool':item['codec']='bool'
                    elif field['storage']=='integer':
                        item['allowed_codecs']=[f'u{field["width"]*8}',f'i{field["width"]*8}']
                        item['signedness']='unknown; choose an explicit codec after reviewing values and intended behavior'
                fields[key]=item
        except FormatError as exc:
            if schema_path:raise
            status['extracted_unavailable']=str(exc)
    elif schema_path:raise FormatError('Schema file is missing; run schema-extract')
    if status['verified']:
        fields.update({k:{**v,'curated':True} for k,v in PROFILES[status['observed_tool_sha256']]['fields'].items()})
    return status,fields,types


def inspect_resource(resource, game=None, property_filter=None,schema_path=None):
    status,schemas,types = schema_bundle(game,schema_path)
    leaves = resource.leaves()
    keys = set(leaves)
    keys |= {k for k,v in schemas.items() if int(k.split('/')[0],16) in resource.fields and
             ('default' in v or v.get('storage_validated'))}
    items = []
    for key in sorted(keys):
        if property_filter and property_filter not in key:
            known_name = schemas.get(key, {}).get("name", "")
            if property_filter.casefold() not in known_name.casefold():
                continue
        item = {"property": key, **describe(leaves.get(key))}
        schema = schemas.get(key)
        if schema:
            item["schema"] = schema
            if schema.get("codec") == "u32":
                raw = leaves.get(key)
                if raw is None:
                    value = schema["default"]
                    item["effective_source"] = "verified_default"
                    item["meaning"] = "omitted; effective value comes from the verified build schema"
                elif len(raw) == 4:
                    value = int.from_bytes(raw, "little")
                    item["effective_source"] = "serialized"
                else:
                    value = None
                item["effective_value"] = value
                item["effective_label"] = schema["enum"].get(value)
            elif schema.get('storage_validated'):
                raw=leaves.get(key)
                if raw is None:
                    raw=bytes.fromhex(schema['default_hex'])
                    item['effective_source']='verified_serializer_default'
                    item['meaning']='omitted; serializer default for this exact editor build'
                else:item['effective_source']='serialized'
                if len(raw)!=schema['width'] or schema['storage']=='bool' and raw not in (b'\0',b'\1'):
                    item['schema_mismatch']='serialized value does not match validated storage'
                elif schema['storage']=='bool':item['effective_value']=bool(raw[0])
                else:
                    item['integer_interpretations']={'unsigned':int.from_bytes(raw,'little'),
                                                     'signed':int.from_bytes(raw,'little',signed=True)}
        items.append(item)
    identity=resource.identity()
    if identity['type_id'] in types:identity['editor_type']=types[identity['type_id']]
    return {"resource": identity, "schema_status": status, "properties": items}
