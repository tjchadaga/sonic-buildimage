from .manager import Manager
from .log import log_err, log_debug, log_notice
import re
from swsscommon import swsscommon

class DeviceGlobalCfgMgr(Manager):
    """This class responds to change in device-specific state"""

    def __init__(self, common_objs, db, table):
        """
        Initialize the object
        :param common_objs: common object dictionary
        :param db: name of the db
        :param table: name of the table in the db
        """
        self.switch_type = ""
        self.directory = common_objs['directory']
        self.cfg_mgr = common_objs['cfg_mgr']
        self.constants = common_objs['constants']
        self.tsa_template = common_objs['tf'].from_file("bgpd/tsa/bgpd.tsa.isolate.conf.j2")
        self.tsb_template = common_objs['tf'].from_file("bgpd/tsa/bgpd.tsa.unisolate.conf.j2")
        self.directory.subscribe([("CONFIG_DB", swsscommon.CFG_DEVICE_METADATA_TABLE_NAME, "localhost/switch_type"),], self.on_switch_type_change)
        super(DeviceGlobalCfgMgr, self).__init__(
            common_objs,
            [],
            db,
            table,
        )

    def on_switch_type_change(self):
        log_debug("DeviceGlobalCfgMgr:: Switch type update handler")
        if self.directory.path_exist("CONFIG_DB", swsscommon.CFG_DEVICE_METADATA_TABLE_NAME, "localhost/switch_type"):
            self.switch_type = self.directory.get_slot("CONFIG_DB", swsscommon.CFG_DEVICE_METADATA_TABLE_NAME)["localhost"]["switch_type"]
        log_debug("DeviceGlobalCfgMgr:: Switch type: %s" % self.switch_type)

    def set_handler(self, key, data):
        log_debug("DeviceGlobalCfgMgr:: set handler")
        if self.switch_type:
            log_debug("DeviceGlobalCfgMgr:: Switch type: %s" % self.switch_type)
        """ Handle device tsa_enabled state change """
        if not data:
            log_err("DeviceGlobalCfgMgr:: data is None")
            return False
        
        self.cfg_mgr.commit()
        self.cfg_mgr.update()       
            
        if "tsa_enabled" in data and "external_only" in data:
            self.directory.put(self.db_name, self.table_name, "external_only", data["external_only"])
            self.directory.put(self.db_name, self.table_name, "tsa_enabled", data["tsa_enabled"])
            self.isolate_unisolate_device(data["tsa_enabled"], data["external_only"])
            return True
        if "external_only" in data:
            self.directory.put(self.db_name, self.table_name, "external_only", data["external_only"])
            if self.directory.path_exist("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME, "tsa_enabled"):
                tsa_enabled = self.directory.get_slot("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME)["tsa_enabled"]
                if tsa_enabled == True:
                    self.isolate_unisolate_device(data["tsa_enabled"], external_only)
            return True
        elif "tsa_enabled" in data:
            self.directory.put(self.db_name, self.table_name, "tsa_enabled", data["tsa_enabled"])
            if self.directory.path_exist("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME, "external_only"):
                external_only = self.directory.get_slot("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME)["external_only"]
                self.isolate_unisolate_device(data["tsa_enabled"], external_only)
            return True

        return False

    def del_handler(self, key):
        log_debug("DeviceGlobalCfgMgr:: del handler")
        return True

    def check_state_and_get_tsa_routemaps(self, cfg):
        """ API to get TSA route-maps if device is isolated"""
        cmd = ""

        if self.directory.path_exist("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME, "tsa_enabled") \
        and self.directory.path_exist("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME, "external_only"):
            tsa_status = self.directory.get_slot("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME)["tsa_enabled"]
            external_only = self.directory.get_slot("CONFIG_DB", swsscommon.CFG_BGP_DEVICE_GLOBAL_TABLE_NAME)["external_only"]
            if tsa_status == "true":
                cmds = cfg.replace("#012", "\n").split("\n")
                log_notice("DeviceGlobalCfgMgr:: Device is isolated. Applying TSA route-maps")
                if external_only == "true":
                    log_notice("DeviceGlobalCfgMgr:: Device external neighbors isolated")
                    cmd = self.get_ts_routemaps(cmds, self.tsa_template, nbrs="external")
                    log_notice("DeviceGlobalCfgMgr:: Device internal neighbors un-isolated")
                    cmd = self.get_ts_routemaps(cmds, self.tsa_template, nbrs="external")
                else:
                    cmd = self.get_ts_routemaps(cmds, self.tsa_template)
                    log_notice("DeviceGlobalCfgMgr:: Device is isolated. Applying TSA route-maps")
        return cmd

    def isolate_unisolate_device(self, tsa_status, external_only):
        """ API to get TSA/TSB route-maps and apply configuration"""
        cmd = "\n"
        if tsa_status == "true" and external_only == "true":
            log_notice("DeviceGlobalCfgMgr:: Device external neighbors isolated")
            cmd += self.get_ts_routemaps(self.cfg_mgr.get_text(), self.tsa_template, nbrs="external")
            log_notice("DeviceGlobalCfgMgr:: Device internal neighbors un-isolated")
            cmd += self.get_ts_routemaps(self.cfg_mgr.get_text(), self.tsb_template, nbrs="internal")
        elif tsa_status == "true":
            log_notice("DeviceGlobalCfgMgr:: Device isolated. Executing TSA")
            cmd += self.get_ts_routemaps(self.cfg_mgr.get_text(), self.tsa_template)
        elif tsa_status == "false":
            log_notice("DeviceGlobalCfgMgr:: Device un-isolated. Executing TSB")
            cmd += self.get_ts_routemaps(self.cfg_mgr.get_text(), self.tsb_template)

        self.cfg_mgr.push(cmd)
        log_debug("DeviceGlobalCfgMgr::Done")

    def get_ts_routemaps(self, cmds, ts_template, nbrs="all"):
        if not cmds:
            return ""

        route_map_names = self.__extract_out_route_map_names(cmds, nbrs)
        return self.__generate_routemaps_from_template(route_map_names, ts_template)

    def __generate_routemaps_from_template(self, route_map_names, template):
        cmd = "\n"
        for rm in sorted(route_map_names):
            # For packet-based chassis, the bgp session between the linecards are also considered internal sessions 
            # While isolating a single linecard, these sessions should not be skipped
            if "_INTERNAL_" in rm and self.switch_type != "chassis-packet":
                continue            
            if "V4" in rm:
                ipv="V4" ; ipp="ip"
            elif "V6" in rm:
                ipv="V6" ; ipp="ipv6"                
            else:
                continue                        
            cmd += template.render(route_map_name=rm,ip_version=ipv,ip_protocol=ipp, constants=self.constants)
            cmd += "\n"
        return cmd

    def __extract_out_route_map_names(self, cmds, nbrs):
        route_map_names = set() 
        out_route_map = re.compile(r'^\s*neighbor \S+ route-map (\S+) out$')
        for line in cmds:
            result = out_route_map.match(line)
            if result:
                rm = result.group(1)
                if "VOQ" in rm or "_INTERNAL_" in rm:
                    if nbrs == "internal" or nbrs == "all":
                        route_map_names.add(rm)
                else:
                    if nbrs == "external" or nbrs == "all":
                        route_map_names.add(rm)
        return route_map_names

