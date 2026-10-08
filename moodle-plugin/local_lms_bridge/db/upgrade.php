<?php
defined('MOODLE_INTERNAL') || die();

function xmldb_local_lms_bridge_upgrade($oldversion): bool {
    global $DB, $CFG;
    if ($oldversion < 2026100801) {
        $file = new xmldb_file($CFG->dirroot . '/local/lms_bridge/db/install.xml');
        $file->loadXMLStructure();
        $table = $file->getStructure()->getTable('local_lms_bridge_requests');
        $dbman = $DB->get_manager();
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }
        upgrade_plugin_savepoint(true, 2026100801, 'local', 'lms_bridge');
    }
    return true;
}
