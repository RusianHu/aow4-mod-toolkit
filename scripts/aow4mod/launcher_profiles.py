"""Reviewed AoW4 loose-Mod launcher schema (no user data)."""
PROFILE = {'name': 'paradox-2025.12-aow4', 'schema_sha256': '71de1b614e13697c98f3f3f5a263c5ea0da12a9a959de105408b9a6785cf3bb5', 'app_asar_sha256': '104ecf36fe8bab69d6c79d0478b415d925e96faee08faa27caa56b43102a7a7f', 'table_sql': {'mods': 'CREATE TABLE `mods` (`id` char(36) not null, `pdxId` varchar(255), `steamId` varchar(255), `gameRegistryId` text, `name` varchar(255), `displayName` varchar(255), "descriptionDeprecated" text, `thumbnailUrl` text, `thumbnailPath` text, `version` varchar(255), `tags` json default \'[]\', `requiredVersion` varchar(255), `arch` text, `os` text, `repositoryPath` text, `dirPath` text, `archivePath` text, `status` text not null, `source` text not null, `cause` text null default null, `timeUpdated` integer, `isNew` boolean, `createdDate` datetime, `subscribedDate` datetime, `size` integer, `metadataId` varchar(255), `remotePdxId` varchar(255), `remoteSteamId` varchar(255), `metadataVersion` varchar(255), `isMetadataApplied` boolean not null default \'0\', `metadataStatus` text check (`metadataStatus` in (\'not_applied\', \'missing\', \'parsing_error\', \'applied\', \'validation_error\')) not null default \'not_applied\', `metadataGameId` varchar(255), `descriptionPdx` varchar(255), `descriptionSteam` varchar(255), `shortDescriptionPdx` varchar(255), `keepLatest` boolean not null default \'1\', `userVersion` boolean null default null, primary key (`id`))', 'playsets': "CREATE TABLE playsets (\n        id char(36) not null,\n        name varchar(255) not null,\n        isActive boolean,\n        loadOrder varchar(255),\n        pdxId int,\n        pdxUserId char(36),\n        createdOn datetime not null,\n        updatedOn datetime,\n        syncedOn datetime,\n        lastServerChecksum text,\n        isRemoved boolean not null default false, `hasNotApprovedChanges` boolean not null default '0', `syncState` varchar(255), `state` text check (`state` in ('private', 'removedByUser', 'public', 'disabledByManager')) not null default 'private', `owned` boolean not null default '1', `author` varchar(255) not null default '', `subscribersCount` integer not null default '0', `ratingsCount` integer not null default '0', `thumbnailFileUrl` varchar(255), `description` varchar(255) default '', `offDisk` boolean not null default '0',\n        primary key (id),\n        constraint uq_pdxId unique (pdxId)\n      )", 'playsets_mods': "CREATE TABLE `playsets_mods` (`playsetId` char(36) not null, `modId` char(36) not null, `enabled` boolean default '1', `position` integer, foreign key(`playsetId`) references `playsets`(`id`) on delete CASCADE, foreign key(`modId`) references `mods`(`id`) on delete CASCADE)"}}

PROFILE["migration_names"] = ['initialSchema', 'addIsNewColumn', 'addPrimaryToPlaysetMods', 'addLoadOrderColumn', 'addCreatedAndSubscribedDate', 'addSizeColumn', 'modsDependencies', 'modStatusEnumColumnType', 'removeEnumChecks', 'addDlcTable', 'updateDlcDependenciesId', 'extendPlaysetsTable', 'modifyPositionToInteger', 'addHasNotApprovedChangesColumn', 'addModLFv11Columns', 'migrateIdToRemoteId', 'playsetsSyncState', 'addMetadataRelationshipTable', 'addIsMetadataAppliedColumn', 'migrateIsMetadataAppliedToMetadataStatus', 'addModShortDescription', 'addKeyValuePairsTable', 'addUgcTable', 'addKeepLatestColumnToModsTable', 'addUserVersionColumnToModsTable', 'addPlaysetSharingColumns', 'AddOffDiskColumnToPlaysetsTable']
PROFILE["require_migrations"] = True
PROFILE["version"] = "2025.12"

# Each archive is reviewed against its own migration registry. Never merge
# registries across versions merely because their owned tables happen to match.
# 2026.12: official remaining migrations executed against an unmodified 28-entry
# database copy; owned/DLC/ownership schemas and all 36 entries are bound together.
PROFILE_2026_12 = {'name': 'paradox-2026.12-aow4',
 'version': '2026.12',
 'schema_tables': ('mods', 'playsets', 'playsets_mods', 'playsets_dlcs', 'transient_ownership'),
 'schema_sha256': '4bc77a44038cfa89c6829931ea81f1647fc2d864096a90b7d2094ebaa60f70fe',
 'app_asar_sha256': '97e4967d91256fbea96b98567e43bfadad5b2b1957d52b71981ce984156cc506',
 'table_sql': {'mods': 'CREATE TABLE `mods` (`id` char(36) not null, `pdxId` varchar(255), `steamId` '
                       'varchar(255), `gameRegistryId` text, `name` varchar(255), `displayName` '
                       'varchar(255), "descriptionDeprecated" text, `thumbnailUrl` text, `thumbnailPath` '
                       "text, `version` varchar(255), `tags` json default '[]', `requiredVersion` "
                       'varchar(255), `arch` text, `os` text, `repositoryPath` text, `dirPath` text, '
                       '`archivePath` text, `status` text not null, `source` text not null, `cause` text '
                       'null default null, `timeUpdated` integer, `isNew` boolean, `createdDate` datetime, '
                       '`subscribedDate` datetime, `size` integer, `metadataId` varchar(255), `remotePdxId` '
                       'varchar(255), `remoteSteamId` varchar(255), `metadataVersion` varchar(255), '
                       "`isMetadataApplied` boolean not null default '0', `metadataStatus` text check "
                       "(`metadataStatus` in ('not_applied', 'missing', 'parsing_error', 'applied', "
                       "'validation_error')) not null default 'not_applied', `metadataGameId` varchar(255), "
                       '`descriptionPdx` varchar(255), `descriptionSteam` varchar(255), '
                       "`shortDescriptionPdx` varchar(255), `keepLatest` boolean not null default '1', "
                       '`userVersion` boolean null default null, `remotePdxUserId` varchar(255) null, '
                       '`remoteSteamUserId` varchar(255) null, `state` varchar(255) null, primary key '
                       '(`id`))',
               'playsets': 'CREATE TABLE playsets (\n'
                           '        id char(36) not null,\n'
                           '        name varchar(255) not null,\n'
                           '        isActive boolean,\n'
                           '        loadOrder varchar(255),\n'
                           '        pdxId int,\n'
                           '        pdxUserId char(36),\n'
                           '        createdOn datetime not null,\n'
                           '        updatedOn datetime,\n'
                           '        syncedOn datetime,\n'
                           '        "deprecatedLastServerChecksum" text,\n'
                           '        isRemoved boolean not null default false, `hasNotApprovedChanges` '
                           "boolean not null default '0', `syncState` varchar(255), `state` text check "
                           "(`state` in ('private', 'removedByUser', 'public', 'disabledByManager')) not "
                           "null default 'private', `owned` boolean not null default '1', `author` "
                           "varchar(255) not null default '', `subscribersCount` integer not null default "
                           '\'0\', `ratingsCount` integer not null default \'0\', "coverImagePath" '
                           "varchar(255), `description` varchar(255) default '', `offDisk` boolean not null "
                           "default '0', `version` varchar(255) null, `lastSyncAttemptAt` datetime null, "
                           '`coverImageUpdatedOn` datetime null, `lastUsedAt` datetime null,\n'
                           '        primary key (id),\n'
                           '        constraint uq_pdxId unique (pdxId)\n'
                           '      )',
               'playsets_mods': 'CREATE TABLE `playsets_mods` (`playsetId` char(36) not null, `modId` '
                                "char(36) not null, `enabled` boolean default '1', `position` integer, "
                                'foreign key(`playsetId`) references `playsets`(`id`) on delete CASCADE, '
                                'foreign key(`modId`) references `mods`(`id`) on delete CASCADE)',
               'transient_ownership': 'CREATE TABLE `transient_ownership` (`id` char(36) not null, '
                                      '`remoteId` varchar(255) not null, `endDate` datetime not null, '
                                      '`userId` char(36) not null, `username` varchar(255), primary key '
                                      '(`id`))',
               'playsets_dlcs': 'CREATE TABLE `playsets_dlcs` (`playsetId` char(36) not null, `dlcId` text '
                                "not null, `enabled` boolean not null default '0', foreign key(`playsetId`) "
                                'references `playsets`(`id`) on delete CASCADE, primary key (`playsetId`, '
                                '`dlcId`))'},
 'migration_names': ['initialSchema',
                     'addIsNewColumn',
                     'addPrimaryToPlaysetMods',
                     'addLoadOrderColumn',
                     'addCreatedAndSubscribedDate',
                     'addSizeColumn',
                     'modsDependencies',
                     'modStatusEnumColumnType',
                     'removeEnumChecks',
                     'addDlcTable',
                     'updateDlcDependenciesId',
                     'extendPlaysetsTable',
                     'modifyPositionToInteger',
                     'addHasNotApprovedChangesColumn',
                     'addModLFv11Columns',
                     'migrateIdToRemoteId',
                     'playsetsSyncState',
                     'addMetadataRelationshipTable',
                     'addIsMetadataAppliedColumn',
                     'migrateIsMetadataAppliedToMetadataStatus',
                     'addModShortDescription',
                     'addKeyValuePairsTable',
                     'addUgcTable',
                     'addKeepLatestColumnToModsTable',
                     'addUserVersionColumnToModsTable',
                     'addPlaysetSharingColumns',
                     'AddOffDiskColumnToPlaysetsTable',
                     'AddTransientOwnershipTable',
                     'addRemoteUserIdsToMods',
                     'addVersionToPlaysets',
                     'addPlaysetsDlcsTable',
                     'removeLastServerChecksumFromPlaysets',
                     'addLastSyncAttemptAtToPlaysets',
                     'adjustPlaysetCoverImageColumns',
                     'addStateToMod',
                     'addLastUsedAtToPlaysets'],
 'require_migrations': True}

PROFILE_2026_12['index_sql'] = [
    'CREATE INDEX `playsets_dlcs_dlcid_index` on `playsets_dlcs` (`dlcId`)',
    'CREATE INDEX `playsets_dlcs_playsetid_index` on `playsets_dlcs` (`playsetId`)',
    'CREATE UNIQUE INDEX `transient_ownership_remoteid_userid_unique` on `transient_ownership` (`remoteId`, `userId`)',
]

# 2026.13 official registry adds exactly this migration and two nullable cache
# columns. The other owned tables/indexes were compared with the installed DB.
# Membership/order changes invalidate the conflict result for that playset only.
PROFILE_2026_13 = {
    **PROFILE_2026_12,
    'name':'paradox-2026.13-aow4', 'version':'2026.13',
    'schema_sha256':'2dda38e45475a91928ebe772d9f6864897a1c6c57220dec7fc0270af62f2982c',
    'app_asar_sha256':'b10d6cab49e7b6154723efa42cf2bd281746ca25cba464a11ef3952aa7c5e226',
    'table_sql':{**PROFILE_2026_12['table_sql'],
        'playsets':PROFILE_2026_12['table_sql']['playsets'].replace(
            '`lastUsedAt` datetime null,',
            '`lastUsedAt` datetime null, `modConflictCompositionHash` varchar(255) null, `modConflictResult` json null,')},
    'migration_names':[*PROFILE_2026_12['migration_names'],'addModConflictResultToPlaysets'],
    'invalidate_playset_cache_columns':('modConflictCompositionHash','modConflictResult'),
}

PROFILES = (PROFILE, PROFILE_2026_12, PROFILE_2026_13)
