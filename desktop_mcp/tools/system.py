"""System operations MCP tools for Windows desktop control."""

from __future__ import annotations

import glob
import hashlib
import os
import shutil
import socket
import subprocess
import zipfile
from functools import wraps
from pathlib import Path
from typing import Any

import psutil


from ..app import mcp
from ..helpers import ensure_windows as _ensure_windows
from ..runtime import record_event


def ensure_windows(fn):
    """Decorator to ensure function runs on Windows."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _ensure_windows()
        return fn(*args, **kwargs)
    return wrapper
@ensure_windows
def list_directory(path: str) -> dict[str, Any]:
    """List contents of a directory."""
    target_path = Path(path)
    if not target_path.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not target_path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")
    
    items = []
    for item in target_path.iterdir():
        try:
            stat = item.stat()
            items.append({
                "name": item.name,
                "path": str(item),
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size if item.is_file() else 0,
                "modified": stat.st_mtime,
            })
        except (PermissionError, OSError):
            items.append({
                "name": item.name,
                "path": str(item),
                "type": "unknown",
                "size": 0,
                "modified": 0,
            })
    
    record_event("list_directory", path=path, count=len(items))
    return {"path": path, "count": len(items), "items": items}
@ensure_windows
def read_file(path: str, max_size: int = 1048576) -> dict[str, Any]:
    """Read contents of a text file."""
    target_path = Path(path)
    if not target_path.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target_path.is_file():
        raise ValueError(f"Path is not a file: {path}")
    
    if target_path.stat().st_size > max_size:
        raise ValueError(f"File too large (max {max_size} bytes)")
    
    try:
        content = target_path.read_text(encoding="utf-8", errors="ignore")
    except UnicodeDecodeError:
        content = target_path.read_text(encoding="latin-1", errors="ignore")
    
    record_event("read_file", path=path, size=len(content))
    return {"path": path, "size": len(content), "content": content}
@ensure_windows
def write_file(path: str, content: str, create_dirs: bool = True) -> dict[str, Any]:
    """Write content to a text file."""
    target_path = Path(path)
    
    if create_dirs:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    
    target_path.write_text(content, encoding="utf-8")
    
    record_event("write_file", path=path, size=len(content))
    return {"path": path, "size": len(content), "written": True}
@ensure_windows
def delete_file(path: str) -> dict[str, Any]:
    """Delete a file or directory."""
    target_path = Path(path)
    if not target_path.exists():
        raise ValueError(f"Path does not exist: {path}")
    
    if target_path.is_dir():
        shutil.rmtree(target_path)
    else:
        target_path.unlink()
    
    record_event("delete_file", path=path)
    return {"path": path, "deleted": True}
@ensure_windows
def create_directory(path: str) -> dict[str, Any]:
    """Create a directory."""
    target_path = Path(path)
    target_path.mkdir(parents=True, exist_ok=True)
    
    record_event("create_directory", path=path)
    return {"path": path, "created": True}
@ensure_windows
def get_system_info() -> dict[str, Any]:
    """Get system information."""
    cpu_info = {
        "percent": psutil.cpu_percent(interval=0.5),
        "count": psutil.cpu_count(logical=True),
        "count_physical": psutil.cpu_count(logical=False),
    }
    
    memory = psutil.virtual_memory()
    memory_info = {
        "total": memory.total,
        "available": memory.available,
        "percent": memory.percent,
        "used": memory.used,
    }
    
    disk = psutil.disk_usage("/")
    disk_info = {
        "total": disk.total,
        "used": disk.used,
        "free": disk.free,
        "percent": disk.percent,
    }
    
    record_event("get_system_info")
    return {
        "cpu": cpu_info,
        "memory": memory_info,
        "disk": disk_info,
    }
@ensure_windows
def list_processes() -> dict[str, Any]:
    """List running processes."""
    processes = []
    for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent"]):
        try:
            processes.append({
                "pid": proc.info["pid"],
                "name": proc.info["name"],
                "username": proc.info.get("username", ""),
                "cpu_percent": proc.info.get("cpu_percent", 0),
                "memory_percent": proc.info.get("memory_percent", 0),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    record_event("list_processes", count=len(processes))
    return {"count": len(processes), "processes": processes}
@ensure_windows
def kill_process(pid: int) -> dict[str, Any]:
    """Kill a process by PID."""
    try:
        proc = psutil.Process(pid)
        proc.kill()
        record_event("kill_process", pid=pid)
        return {"pid": pid, "killed": True}
    except psutil.NoSuchProcess:
        raise ValueError(f"Process not found: {pid}")
    except psutil.AccessDenied:
        raise ValueError(f"Access denied to process: {pid}")
@ensure_windows
def get_environment_variables() -> dict[str, Any]:
    """Get all environment variables."""
    env_vars = dict(os.environ)
    record_event("get_environment_variables", count=len(env_vars))
    return {"count": len(env_vars), "variables": env_vars}
@ensure_windows
def get_environment_variable(name: str) -> dict[str, Any]:
    """Get a specific environment variable."""
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"Environment variable not found: {name}")
    
    record_event("get_environment_variable", name=name)
    return {"name": name, "value": value}
@ensure_windows
def set_environment_variable(name: str, value: str, permanent: bool = False) -> dict[str, Any]:
    """Set an environment variable."""
    os.environ[name] = value
    
    if permanent:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            winreg.CloseKey(key)
            # Notify system of change
            import ctypes
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0, 5000, None)
        except Exception as e:
            raise RuntimeError(f"Failed to set permanent environment variable: {e}")
    
    record_event("set_environment_variable", name=name, permanent=permanent)
    return {"name": name, "value": value, "permanent": permanent}
@ensure_windows
def ping_host(host: str, count: int = 4) -> dict[str, Any]:
    """Ping a host to test connectivity."""
    try:
        result = subprocess.run(
            ["ping", "-n", str(count), host],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse output
        lines = result.stdout.split("\n")
        success = result.returncode == 0
        
        record_event("ping_host", host=host, count=count, success=success)
        return {
            "host": host,
            "count": count,
            "success": success,
            "output": result.stdout,
            "error": result.stderr if result.stderr else None,
        }
    except subprocess.TimeoutExpired:
        raise ValueError(f"Ping timeout for host: {host}")
    except Exception as e:
        raise RuntimeError(f"Ping failed: {e}")
@ensure_windows
def get_network_info() -> dict[str, Any]:
    """Get network information."""
    interfaces = []
    for name, addrs in psutil.net_if_addrs().items():
        interface_info = {
            "name": name,
            "addresses": [],
        }
        for addr in addrs:
            interface_info["addresses"].append({
                "family": str(addr.family),
                "address": addr.address,
                "netmask": addr.netmask,
                "broadcast": addr.broadcast,
            })
        interfaces.append(interface_info)
    
    io_counters = psutil.net_io_counters()
    io_info = {
        "bytes_sent": io_counters.bytes_sent,
        "bytes_recv": io_counters.bytes_recv,
        "packets_sent": io_counters.packets_sent,
        "packets_recv": io_counters.packets_recv,
    }
    
    connections = []
    for conn in psutil.net_connections(kind="inet"):
        connections.append({
            "local_address": conn.laddr[0] if conn.laddr else None,
            "local_port": conn.laddr[1] if conn.laddr else None,
            "remote_address": conn.raddr[0] if conn.raddr else None,
            "remote_port": conn.raddr[1] if conn.raddr else None,
            "status": conn.status,
            "pid": conn.pid,
        })
    
    record_event("get_network_info")
    return {
        "interfaces": interfaces,
        "io_counters": io_info,
        "connections": connections[:100],  # Limit to 100 connections
    }
@ensure_windows
def check_port(host: str, port: int, timeout: int = 5) -> dict[str, Any]:
    """Check if a port is open on a host."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        
        is_open = result == 0
        record_event("check_port", host=host, port=port, is_open=is_open)
        return {
            "host": host,
            "port": port,
            "open": is_open,
        }
    except socket.timeout:
        raise ValueError(f"Connection timeout for {host}:{port}")
    except Exception as e:
        raise RuntimeError(f"Port check failed: {e}")
@ensure_windows
def resolve_hostname(hostname: str) -> dict[str, Any]:
    """Resolve a hostname to IP addresses."""
    try:
        addresses = socket.getaddrinfo(hostname, None)
        ips = list(set([addr[4][0] for addr in addresses if addr[4][0]]))
        
        record_event("resolve_hostname", hostname=hostname, count=len(ips))
        return {
            "hostname": hostname,
            "addresses": ips,
        }
    except socket.gaierror:
        raise ValueError(f"Failed to resolve hostname: {hostname}")
    except Exception as e:
        raise RuntimeError(f"Hostname resolution failed: {e}")
@ensure_windows
def read_registry_key(key_path: str, value_name: str | None = None) -> dict[str, Any]:
    """Read a value from Windows registry."""
    import winreg
    
    # Parse key path (e.g., "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft")
    parts = key_path.split("\\", 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid registry key path: {key_path}")
    
    root_key_name = parts[0]
    sub_key = parts[1]
    
    # Map root key names to winreg constants
    root_key_map = {
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_USERS": winreg.HKEY_USERS,
        "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
    }
    
    if root_key_name not in root_key_map:
        raise ValueError(f"Invalid root key: {root_key_name}")
    
    root_key = root_key_map[root_key_name]
    
    try:
        with winreg.OpenKey(root_key, sub_key) as key:
            if value_name:
                # Read specific value
                value, value_type = winreg.QueryValueEx(key, value_name)
                type_name = {
                    winreg.REG_SZ: "REG_SZ",
                    winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
                    winreg.REG_BINARY: "REG_BINARY",
                    winreg.REG_DWORD: "REG_DWORD",
                    winreg.REG_DWORD_BIG_ENDIAN: "REG_DWORD_BIG_ENDIAN",
                    winreg.REG_LINK: "REG_LINK",
                    winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
                    winreg.REG_RESOURCE_LIST: "REG_RESOURCE_LIST",
                    winreg.REG_FULL_RESOURCE_DESCRIPTOR: "REG_FULL_RESOURCE_DESCRIPTOR",
                    winreg.REG_RESOURCE_REQUIREMENTS_LIST: "REG_RESOURCE_REQUIREMENTS_LIST",
                    winreg.REG_QWORD: "REG_QWORD",
                }.get(value_type, str(value_type))
                
                record_event("read_registry_key", key_path=key_path, value_name=value_name)
                return {
                    "key_path": key_path,
                    "value_name": value_name,
                    "value": value,
                    "type": type_name,
                }
            else:
                # List all values
                values = []
                try:
                    i = 0
                    while True:
                        name, value, value_type = winreg.EnumValue(key, i)
                        type_name = {
                            winreg.REG_SZ: "REG_SZ",
                            winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
                            winreg.REG_BINARY: "REG_BINARY",
                            winreg.REG_DWORD: "REG_DWORD",
                            winreg.REG_DWORD_BIG_ENDIAN: "REG_DWORD_BIG_ENDIAN",
                            winreg.REG_LINK: "REG_LINK",
                            winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
                            winreg.REG_QWORD: "REG_QWORD",
                        }.get(value_type, str(value_type))
                        values.append({
                            "name": name,
                            "value": value,
                            "type": type_name,
                        })
                        i += 1
                except OSError:
                    pass
                
                record_event("read_registry_key", key_path=key_path, count=len(values))
                return {
                    "key_path": key_path,
                    "count": len(values),
                    "values": values,
                }
    except FileNotFoundError:
        raise ValueError(f"Registry key not found: {key_path}")
    except Exception as e:
        raise RuntimeError(f"Failed to read registry key: {e}")
@ensure_windows
def write_registry_value(key_path: str, value_name: str, value: str, value_type: str = "REG_SZ") -> dict[str, Any]:
    """Write a value to Windows registry."""
    import winreg
    
    # Parse key path
    parts = key_path.split("\\", 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid registry key path: {key_path}")
    
    root_key_name = parts[0]
    sub_key = parts[1]
    
    # Map root key names
    root_key_map = {
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_USERS": winreg.HKEY_USERS,
        "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
    }
    
    if root_key_name not in root_key_map:
        raise ValueError(f"Invalid root key: {root_key_name}")
    
    root_key = root_key_map[root_key_name]
    
    # Map value type names
    type_map = {
        "REG_SZ": winreg.REG_SZ,
        "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
        "REG_DWORD": winreg.REG_DWORD,
        "REG_QWORD": winreg.REG_QWORD,
    }
    
    if value_type not in type_map:
        raise ValueError(f"Invalid value type: {value_type}")
    
    reg_type = type_map[value_type]
    
    try:
        with winreg.CreateKey(root_key, sub_key) as key:
            winreg.SetValueEx(key, value_name, 0, reg_type, value)
        
        record_event("write_registry_value", key_path=key_path, value_name=value_name)
        return {
            "key_path": key_path,
            "value_name": value_name,
            "value": value,
            "type": value_type,
            "written": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to write registry value: {e}")
@ensure_windows
def delete_registry_value(key_path: str, value_name: str) -> dict[str, Any]:
    """Delete a value from Windows registry."""
    import winreg
    
    # Parse key path
    parts = key_path.split("\\", 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid registry key path: {key_path}")
    
    root_key_name = parts[0]
    sub_key = parts[1]
    
    root_key_map = {
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_USERS": winreg.HKEY_USERS,
        "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
    }
    
    if root_key_name not in root_key_map:
        raise ValueError(f"Invalid root key: {root_key_name}")
    
    root_key = root_key_map[root_key_name]
    
    try:
        with winreg.OpenKey(root_key, sub_key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
        
        record_event("delete_registry_value", key_path=key_path, value_name=value_name)
        return {
            "key_path": key_path,
            "value_name": value_name,
            "deleted": True,
        }
    except FileNotFoundError:
        raise ValueError(f"Registry key or value not found: {key_path}\\{value_name}")
    except Exception as e:
        raise RuntimeError(f"Failed to delete registry value: {e}")
@ensure_windows
def list_windows_services() -> dict[str, Any]:
    """List all Windows services."""
    try:
        import win32service
        import win32serviceutil
        
        # Get service manager
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
        
        services = []
        service_type = win32service.SERVICE_WIN32
        service_state = win32service.SERVICE_STATE_ALL
        
        # Enumerate services
        service_list = win32service.EnumServicesStatus(scm, service_type, service_state)
        
        for service_name, display_name, status in service_list:
            services.append({
                "name": service_name,
                "display_name": display_name,
                "status": status,
            })
        
        win32service.CloseServiceHandle(scm)
        
        record_event("list_windows_services", count=len(services))
        return {
            "count": len(services),
            "services": services,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to list Windows services: {e}")
@ensure_windows
def get_service_status(service_name: str) -> dict[str, Any]:
    """Get status of a specific Windows service."""
    try:
        import win32service
        import win32serviceutil
        
        status = win32serviceutil.QueryServiceStatus(service_name)
        
        status_map = {
            win32service.SERVICE_STOPPED: "stopped",
            win32service.SERVICE_START_PENDING: "start_pending",
            win32service.SERVICE_STOP_PENDING: "stop_pending",
            win32service.SERVICE_RUNNING: "running",
            win32service.SERVICE_CONTINUE_PENDING: "continue_pending",
            win32service.SERVICE_PAUSE_PENDING: "pause_pending",
            win32service.SERVICE_PAUSED: "paused",
        }
        
        record_event("get_service_status", service_name=service_name)
        return {
            "service_name": service_name,
            "status": status_map.get(status[1], "unknown"),
            "status_code": status[1],
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get service status: {e}")
@ensure_windows
def start_service(service_name: str) -> dict[str, Any]:
    """Start a Windows service."""
    try:
        import win32serviceutil
        
        win32serviceutil.StartService(service_name)
        
        record_event("start_service", service_name=service_name)
        return {
            "service_name": service_name,
            "started": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to start service: {e}")
@ensure_windows
def stop_service(service_name: str) -> dict[str, Any]:
    """Stop a Windows service."""
    try:
        import win32serviceutil
        
        win32serviceutil.StopService(service_name)
        
        record_event("stop_service", service_name=service_name)
        return {
            "service_name": service_name,
            "stopped": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to stop service: {e}")
@ensure_windows
def restart_service(service_name: str) -> dict[str, Any]:
    """Restart a Windows service."""
    try:
        import win32serviceutil
        
        win32serviceutil.RestartService(service_name)
        
        record_event("restart_service", service_name=service_name)
        return {
            "service_name": service_name,
            "restarted": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to restart service: {e}")
@ensure_windows
def shutdown_computer(force: bool = False, timeout: int = 30) -> dict[str, Any]:
    """Shutdown the computer."""
    try:
        import win32api
        import win32con
        
        win32api.InitiateSystemShutdown(
            None,  # local computer
            None,  # message
            timeout,  # timeout in seconds
            force,  # force close apps
            False,  # don't reboot
        )
        
        record_event("shutdown_computer", force=force, timeout=timeout)
        return {
            "action": "shutdown",
            "force": force,
            "timeout": timeout,
            "initiated": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to initiate shutdown: {e}")
@ensure_windows
def restart_computer(force: bool = False, timeout: int = 30) -> dict[str, Any]:
    """Restart the computer."""
    try:
        import win32api
        import win32con
        
        win32api.InitiateSystemShutdown(
            None,  # local computer
            None,  # message
            timeout,  # timeout in seconds
            force,  # force close apps
            True,  # reboot
        )
        
        record_event("restart_computer", force=force, timeout=timeout)
        return {
            "action": "restart",
            "force": force,
            "timeout": timeout,
            "initiated": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to initiate restart: {e}")
@ensure_windows
def sleep_computer(force: bool = False, timeout: int = 30) -> dict[str, Any]:
    """Put the computer to sleep."""
    try:
        import win32api
        import win32con
        
        # Use SetSuspendState
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        
        record_event("sleep_computer", force=force, timeout=timeout)
        return {
            "action": "sleep",
            "force": force,
            "timeout": timeout,
            "initiated": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to initiate sleep: {e}")
@ensure_windows
def lock_computer() -> dict[str, Any]:
    """Lock the computer."""
    try:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        
        record_event("lock_computer")
        return {
            "action": "lock",
            "initiated": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to lock computer: {e}")
@ensure_windows
def show_notification(title: str, message: str, duration: int = 5) -> dict[str, Any]:
    """Show a system notification (toast)."""
    try:
        # Try using win10toast first
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_notification(
                title=title,
                msg=message,
                duration=duration,
                threaded=True
            )
        except ImportError:
            # Fallback to basic notification using ctypes
            import ctypes
            # This is a simplified notification using Windows message box
            # In a real implementation, you'd want a proper toast notification library
            import win32api
            win32api.MessageBox(0, message, title, 0)
        
        record_event("show_notification", title=title, duration=duration)
        return {
            "title": title,
            "message": message,
            "duration": duration,
            "shown": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to show notification: {e}")
@ensure_windows
def get_clipboard_text() -> dict[str, Any]:
    """Get text from clipboard."""
    try:
        import win32clipboard
        
        win32clipboard.OpenClipboard()
        try:
            text = win32clipboard.GetClipboardData()
        except Exception:
            text = ""
        finally:
            win32clipboard.CloseClipboard()
        
        record_event("get_clipboard_text")
        return {
            "text": text,
            "length": len(text),
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get clipboard text: {e}")
@ensure_windows
def set_clipboard_text(text: str) -> dict[str, Any]:
    """Set text to clipboard."""
    try:
        import win32clipboard
        
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
        finally:
            win32clipboard.CloseClipboard()
        
        record_event("set_clipboard_text", length=len(text))
        return {
            "text": text,
            "length": len(text),
            "set": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to set clipboard text: {e}")
@ensure_windows
def calculate_file_hash(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    """Calculate hash of a file."""
    target_path = Path(path)
    if not target_path.exists():
        raise ValueError(f"File does not exist: {path}")
    if not target_path.is_file():
        raise ValueError(f"Path is not a file: {path}")
    
    # Validate algorithm
    if algorithm not in hashlib.algorithms_available:
        raise ValueError(f"Invalid algorithm: {algorithm}")
    
    try:
        hash_obj = hashlib.new(algorithm)
        with target_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_obj.update(chunk)
        
        hash_value = hash_obj.hexdigest()
        record_event("calculate_file_hash", path=path, algorithm=algorithm)
        return {
            "path": path,
            "algorithm": algorithm,
            "hash": hash_value,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to calculate file hash: {e}")
@ensure_windows
def compress_files(source_path: str, output_path: str) -> dict[str, Any]:
    """Compress files or directories into a ZIP archive."""
    source = Path(source_path)
    if not source.exists():
        raise ValueError(f"Source path does not exist: {source_path}")
    
    output = Path(output_path)
    
    try:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if source.is_file():
                zipf.write(source, source.name)
            else:
                for item in source.rglob("*"):
                    if item.is_file():
                        arcname = item.relative_to(source.parent)
                        zipf.write(item, arcname)
        
        record_event("compress_files", source_path=source_path, output_path=output_path)
        return {
            "source_path": source_path,
            "output_path": output_path,
            "compressed": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to compress files: {e}")
@ensure_windows
def decompress_files(zip_path: str, output_dir: str) -> dict[str, Any]:
    """Decompress a ZIP archive."""
    zip_file = Path(zip_path)
    if not zip_file.exists():
        raise ValueError(f"ZIP file does not exist: {zip_path}")
    
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    
    try:
        with zipfile.ZipFile(zip_file, 'r') as zipf:
            zipf.extractall(output)
        
        record_event("decompress_files", zip_path=zip_path, output_dir=output_dir)
        return {
            "zip_path": zip_path,
            "output_dir": output_dir,
            "decompressed": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to decompress files: {e}")
@ensure_windows
def get_system_uptime() -> dict[str, Any]:
    """Get system uptime."""
    try:
        uptime_seconds = psutil.boot_time()
        import time
        current_time = time.time()
        uptime = current_time - uptime_seconds
        
        # Convert to human-readable format
        days = int(uptime // 86400)
        hours = int((uptime % 86400) // 3600)
        minutes = int((uptime % 3600) // 60)
        
        record_event("get_system_uptime")
        return {
            "uptime_seconds": int(uptime),
            "uptime_human": f"{days}d {hours}h {minutes}m",
            "boot_time": uptime_seconds,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get system uptime: {e}")
@ensure_windows
def get_battery_info() -> dict[str, Any]:
    """Get battery information."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return {
                "battery_present": False,
                "message": "No battery detected",
            }
        
        record_event("get_battery_info")
        return {
            "battery_present": True,
            "percent": battery.percent,
            "power_plugged": battery.power_plugged,
            "seconds_left": battery.secsleft if battery.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED) else None,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get battery info: {e}")
@ensure_windows
def set_desktop_wallpaper(image_path: str) -> dict[str, Any]:
    """Set desktop wallpaper."""
    try:
        import ctypes
        image = Path(image_path)
        if not image.exists():
            raise ValueError(f"Image file does not exist: {image_path}")
        
        SPI_SETDESKWALLPAPER = 20
        SPIF_UPDATEINIFILE = 0x01
        SPIF_SENDWININICHANGE = 0x02
        
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER,
            0,
            str(image.absolute()),
            SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
        )
        
        record_event("set_desktop_wallpaper", image_path=image_path)
        return {
            "image_path": image_path,
            "wallpaper_set": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to set desktop wallpaper: {e}")
@ensure_windows
def create_symbolic_link(source: str, link_name: str) -> dict[str, Any]:
    """Create a symbolic link."""
    try:
        source_path = Path(source)
        link_path = Path(link_name)
        
        if not source_path.exists():
            raise ValueError(f"Source does not exist: {source}")
        
        if link_path.exists():
            raise ValueError(f"Link already exists: {link_name}")
        
        os.symlink(source_path, link_path)
        
        record_event("create_symbolic_link", source=source, link_name=link_name)
        return {
            "source": source,
            "link_name": link_name,
            "created": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to create symbolic link: {e}")
@ensure_windows
def search_files(pattern: str, directory: str = ".", recursive: bool = True) -> dict[str, Any]:
    """Search for files matching a pattern."""
    try:
        dir_path = Path(directory)
        if not dir_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        
        if recursive:
            matches = list(dir_path.rglob(pattern))
        else:
            matches = list(dir_path.glob(pattern))
        
        files = [str(m) for m in matches if m.is_file()]
        
        record_event("search_files", pattern=pattern, directory=directory, count=len(files))
        return {
            "pattern": pattern,
            "directory": directory,
            "count": len(files),
            "files": files,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to search files: {e}")
@ensure_windows
def get_disk_usage(path: str = ".") -> dict[str, Any]:
    """Get disk usage statistics."""
    try:
        target_path = Path(path)
        if not target_path.exists():
            raise ValueError(f"Path does not exist: {path}")
        
        usage = shutil.disk_usage(target_path)
        
        record_event("get_disk_usage", path=path)
        return {
            "path": path,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent_used": round((usage.used / usage.total) * 100, 2),
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get disk usage: {e}")
@ensure_windows
def get_process_details(pid: int) -> dict[str, Any]:
    """Get detailed information about a specific process."""
    try:
        process = psutil.Process(pid)
        
        details = {
            "pid": pid,
            "name": process.name(),
            "status": process.status(),
            "username": process.username(),
            "cpu_percent": process.cpu_percent(),
            "memory_info": {
                "rss": process.memory_info().rss,
                "vms": process.memory_info().vms,
                "percent": process.memory_percent(),
            },
            "create_time": process.create_time(),
            "num_threads": process.num_threads(),
        }
        
        try:
            details["exe"] = process.exe()
        except:
            details["exe"] = None
        
        try:
            details["cwd"] = process.cwd()
        except:
            details["cwd"] = None
        
        try:
            details["cmdline"] = process.cmdline()
        except:
            details["cmdline"] = None
        
        record_event("get_process_details", pid=pid)
        return details
    except psutil.NoSuchProcess:
        raise ValueError(f"Process with PID {pid} does not exist")
    except Exception as e:
        raise RuntimeError(f"Failed to get process details: {e}")


__all__ = [
    "list_directory",
    "read_file",
    "write_file",
    "delete_file",
    "create_directory",
    "get_system_info",
    "list_processes",
    "kill_process",
    "get_environment_variables",
    "get_environment_variable",
    "set_environment_variable",
    "ping_host",
    "get_network_info",
    "check_port",
    "resolve_hostname",
    "read_registry_key",
    "write_registry_value",
    "delete_registry_value",
    "list_windows_services",
    "get_service_status",
    "start_service",
    "stop_service",
    "restart_service",
    "shutdown_computer",
    "restart_computer",
    "sleep_computer",
    "lock_computer",
    "show_notification",
    "calculate_file_hash",
    "compress_files",
    "decompress_files",
    "get_system_uptime",
    "get_battery_info",
    "set_desktop_wallpaper",
    "create_symbolic_link",
    "search_files",
    "get_disk_usage",
    "get_process_details",
]
