param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, WindowsBase, System.Web.Extensions
$code = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Automation;
public static class SnapFlowActions {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h, int index, StringBuilder value, int length, out int needed);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("advapi32.dll")] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll")] static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr info, int length, out int needed);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr sid, uint index);
    static TreeWalker walker = TreeWalker.ControlViewWalker;
    static Regex protectedNames = new Regex(@"password|passphrase|credential|api[ _-]?key|access[ _-]?token|secret|pin\b|one[ -]?time[ -]?code|authentication|captcha", RegexOptions.IgnoreCase);
    static Regex prohibited = new Regex(@"\b(delete|destroy|erase|remove|uninstall|reset|format|wipe|pay|payments?|purchase|buy|checkout|place order|charge|donate|transfer|trade|trading|invest|bank|brokerage|login|log in|sign in|security|execute|run|command|terminal|shell|administrator)\b", RegexOptions.IgnoreCase);
    static Stopwatch timer;
    static bool complete;
    static string limitedReason;
    static List<Dictionary<string, object>> elements;
    static Dictionary<string, AutomationElement> references;
    static JavaScriptSerializer json = new JavaScriptSerializer();
    static int Integrity(int pid) {
        IntPtr process = OpenProcess(0x1000, false, (uint)pid), token = IntPtr.Zero, info = IntPtr.Zero;
        try {
            if (process == IntPtr.Zero || !OpenProcessToken(process, 8, out token)) return -1;
            int needed; GetTokenInformation(token, 25, IntPtr.Zero, 0, out needed);
            if (needed <= 0 || needed > 65536) return -1;
            info = Marshal.AllocHGlobal(needed);
            if (!GetTokenInformation(token, 25, info, needed, out needed)) return -1;
            IntPtr sid = Marshal.ReadIntPtr(info);
            byte count = Marshal.ReadByte(GetSidSubAuthorityCount(sid));
            return count == 0 ? -1 : Marshal.ReadInt32(GetSidSubAuthority(sid, (uint)(count - 1)));
        } finally {
            if (info != IntPtr.Zero) Marshal.FreeHGlobal(info);
            if (token != IntPtr.Zero) CloseHandle(token);
            if (process != IntPtr.Zero) CloseHandle(process);
        }
    }
    static bool Security(long hwnd, int pid) {
        uint currentPid; GetWindowThreadProcessId(new IntPtr(hwnd), out currentPid);
        if (currentPid != pid) return false;
        IntPtr desktop = OpenInputDesktop(0, false, 1);
        if (desktop == IntPtr.Zero) return false;
        try {
            var name = new StringBuilder(256); int needed;
            if (!GetUserObjectInformation(desktop, 2, name, 512, out needed) || name.ToString() != "Default") return false;
        } finally { CloseDesktop(desktop); }
        var self = Process.GetCurrentProcess();
        int mine = Integrity(self.Id), theirs = Integrity(pid);
        return mine >= 0 && mine < 0x3000 && theirs >= 0 && theirs <= mine && Process.GetProcessById(pid).SessionId == self.SessionId;
    }
    static bool Protected(AutomationElement e) {
        if (e.Current.IsPassword) return true; // Before any name/value/pattern access.
        return protectedNames.IsMatch(e.Current.Name ?? "") || protectedNames.IsMatch(e.Current.AutomationId ?? "");
    }
    static string Bound(string value, int limit) { return (value ?? "").Substring(0, Math.Min((value ?? "").Length, limit)); }
    static string Signature(AutomationElement e) {
        return e.Current.ControlType.ProgrammaticName + "|" + Bound(e.Current.AutomationId, 128) + "|" + Bound(e.Current.ClassName, 128);
    }
    static Dictionary<string, object> Describe(AutomationElement e, int pid, long hwnd, List<string> path, List<string> riskContext) {
        var c = e.Current;
        var state = new Dictionary<string, object> {{"enabled", c.IsEnabled}, {"offscreen", c.IsOffscreen}, {"focused", c.HasKeyboardFocus}};
        var actions = new List<string>(); var patterns = new List<string>(); object p;
        if (c.IsKeyboardFocusable) actions.Add("focus");
        if (e.TryGetCurrentPattern(InvokePattern.Pattern, out p)) { actions.Add("invoke"); patterns.Add("Invoke"); }
        if (e.TryGetCurrentPattern(TogglePattern.Pattern, out p)) {
            var s = ((TogglePattern)p).Current.ToggleState;
            state["toggle_state"] = s == ToggleState.Indeterminate ? (object)"indeterminate" : s == ToggleState.On;
            actions.Add("toggle"); patterns.Add("Toggle");
        }
        if (e.TryGetCurrentPattern(SelectionPattern.Pattern, out p)) patterns.Add("Selection");
        if (e.TryGetCurrentPattern(SelectionItemPattern.Pattern, out p)) {
            var s = ((SelectionItemPattern)p).Current;
            state["selected"] = s.IsSelected;
            state["selection_container"] = s.SelectionContainer == null ? null : String.Join(".", s.SelectionContainer.GetRuntimeId());
            actions.Add("select"); patterns.Add("SelectionItem");
        }
        if (e.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out p)) {
            var s = ((ExpandCollapsePattern)p).Current.ExpandCollapseState;
            state["expanded"] = s == ExpandCollapseState.Expanded;
            if (s != ExpandCollapseState.LeafNode) { actions.Add("expand"); actions.Add("collapse"); }
            patterns.Add("ExpandCollapse");
        }
        if ((c.ControlType == ControlType.Edit || c.ControlType == ControlType.ComboBox) && e.TryGetCurrentPattern(ValuePattern.Pattern, out p)) {
            var s = ((ValuePattern)p).Current; state["read_only"] = s.IsReadOnly;
            state["value"] = Bound(s.Value, 4096);
            state["value_truncated"] = s.Value.Length > 4096;
            if (!s.IsReadOnly) actions.Add("set_value"); patterns.Add("Value");
        }
        if (e.TryGetCurrentPattern(RangeValuePattern.Pattern, out p)) {
            var s = ((RangeValuePattern)p).Current; state["read_only"] = s.IsReadOnly;
            state["range_value"] = s.Value; state["minimum"] = s.Minimum; state["maximum"] = s.Maximum;
            if (!s.IsReadOnly) actions.Add("set_range_value"); patterns.Add("RangeValue");
        }
        if (e.TryGetCurrentPattern(ScrollPattern.Pattern, out p)) {
            var s = ((ScrollPattern)p).Current;
            state["scroll_horizontal"] = s.HorizontalScrollPercent; state["scroll_vertical"] = s.VerticalScrollPercent;
            actions.Add("scroll"); patterns.Add("Scroll");
        }
        if (e.TryGetCurrentPattern(ScrollItemPattern.Pattern, out p)) { actions.Add("scroll_into_view"); patterns.Add("ScrollItem"); }
        var r = c.BoundingRectangle;
        string id = String.Join(".", e.GetRuntimeId()); references[id] = e;
        return new Dictionary<string, object> {{"element_id", id}, {"process_id", pid}, {"window_id", hwnd},
            {"control_type", c.ControlType.ProgrammaticName}, {"name", Bound(c.Name, 500)},
            {"automation_id", Bound(c.AutomationId, 128)}, {"class_name", Bound(c.ClassName, 128)},
            {"ancestor_path", path.ToArray()}, {"risk_context", riskContext.ToArray()}, {"bounds", new double[] {r.Left, r.Top, r.Right, r.Bottom}},
            {"protected", false}, {"state", state}, {"capabilities", new Dictionary<string, object> {{"actions", actions}, {"patterns", patterns}}}};
    }
    static void Walk(AutomationElement e, int depth, int pid, long hwnd, List<string> path, List<string> riskContext) {
        if (e == null) return;
        if (depth > 8 || elements.Count >= 300 || timer.ElapsedMilliseconds > 4000) { complete = false; limitedReason = "budget"; return; }
        try {
            if (Protected(e)) return;
            elements.Add(Describe(e, pid, hwnd, path, riskContext));
            var nextPath = new List<string>(path); nextPath.Add(Signature(e));
            var nextRisk = new List<string>(riskContext); nextRisk.Add(Bound(e.Current.Name, 500));
            var child = walker.GetFirstChild(e);
            while (child != null && complete) { Walk(child, depth + 1, pid, hwnd, nextPath, nextRisk); child = walker.GetNextSibling(child); }
        } catch { complete = false; limitedReason = "provider_unavailable"; }
    }
    public static Dictionary<string, object> Capture(long hwnd, int pid) {
        if (!Security(hwnd, pid)) throw new UnauthorizedAccessException();
        string process = Path.GetFileName(Process.GetProcessById(pid).MainModule.FileName);
        foreach (string denied in new string[] {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wsl.exe", "putty.exe", "mstsc.exe", "consent.exe", "credentialuibroker.exe", "logonui.exe", "keepass.exe", "1password.exe", "bitwarden.exe"})
            if (String.Equals(process, denied, StringComparison.OrdinalIgnoreCase)) throw new UnauthorizedAccessException();
        timer = Stopwatch.StartNew(); complete = true; limitedReason = null;
        elements = new List<Dictionary<string, object>>(); references = new Dictionary<string, AutomationElement>();
        var root = AutomationElement.FromHandle(new IntPtr(hwnd));
        if (root.Current.ProcessId != pid || Protected(root)) throw new UnauthorizedAccessException();
        Walk(root, 0, pid, hwnd, new List<string>(), new List<string>());
        return new Dictionary<string, object> {{"pid", pid}, {"hwnd", hwnd}, {"process_name", process},
            {"foreground", GetForegroundWindow().ToInt64()}, {"elements", elements}, {"complete", complete}, {"limit_reason", limitedReason}, {"security_available", true}};
    }
    static bool Same(object a, object b) { return json.Serialize(a) == json.Serialize(b); }
    static void RequireForeground(long hwnd, int pid) {
        if (!Security(hwnd, pid) || GetForegroundWindow().ToInt64() != hwnd) throw new UnauthorizedAccessException();
    }
    static bool Preconditions(object raw, int pid, long hwnd) {
        if (raw == null) return true;
        var conditions = (object[])raw;
        if (conditions.Length > 8) return false;
        foreach (Dictionary<string, object> condition in conditions) {
            var target = (Dictionary<string, object>)condition["target"];
            Dictionary<string, object> match = null; int count = 0;
            foreach (var candidate in elements) {
                bool same = true;
                foreach (string key in new string[] {"control_type", "name", "automation_id", "class_name", "ancestor_path"}) {
                    if (target[key] != null && (!(target[key] is object[]) || ((object[])target[key]).Length > 0) && !Same(target[key], candidate[key])) same = false;
                }
                if (same) { match = candidate; count++; }
            }
            if (count != 1 || match == null) return false;
            var element = references[(string)match["element_id"]];
            if (Protected(element)) return false;
            var state = Describe(element, pid, hwnd, new List<string>(), new List<string>());
            string property = (string)condition["property"];
            object actual;
            if (property == "name") actual = state["name"];
            else {
                var values = (Dictionary<string, object>)state["state"];
                if (!values.ContainsKey(property) || property == "value" && (bool)values["value_truncated"]) return false;
                actual = values[property];
            }
            if (!Same(actual, condition["equals"])) return false;
        }
        return true;
    }
    public static Dictionary<string, object> Execute(long hwnd, int pid, string action, string payload) {
        var approved = (Dictionary<string, object>)json.DeserializeObject(payload);
        var prior = (Dictionary<string, object>)approved["element"];
        object value = approved["value"];
        Capture(hwnd, pid); // Fresh native resolution, never a COM reference from planning.
        if (!complete || GetForegroundWindow().ToInt64() != hwnd) throw new UnauthorizedAccessException();
        Dictionary<string, object> match = null; int count = 0;
        foreach (var candidate in elements) {
            bool identity = true;
            foreach (string key in new string[] {"control_type", "name", "automation_id", "class_name", "ancestor_path", "risk_context"})
                if (!Same(candidate[key], prior[key])) identity = false;
            if (identity) { match = candidate; count++; }
        }
        if (count != 1 || match == null) throw new UnauthorizedAccessException();
        var state = (Dictionary<string, object>)match["state"];
        var oldState = (Dictionary<string, object>)prior["state"];
        foreach (var pair in oldState) {
            if (pair.Key == "focused" && action != "focus") continue;
            if (!state.ContainsKey(pair.Key) || !Same(state[pair.Key], pair.Value)) throw new UnauthorizedAccessException();
        }
        var element = references[(string)match["element_id"]];
        if (Protected(element) || prohibited.IsMatch(element.Current.Name ?? "") || prohibited.IsMatch(element.Current.AutomationId ?? "") ||
                !element.Current.IsEnabled || (element.Current.IsOffscreen && action != "scroll_into_view")) throw new UnauthorizedAccessException();
        // Recheck every ancestor, root ownership, default desktop, integrity and foreground at dispatch.
        var ancestor = element; int hops = 0; bool foundRoot = false;
        while (ancestor != null && hops++ < 10) {
            if (Protected(ancestor) || prohibited.IsMatch(ancestor.Current.Name ?? "") || ancestor.Current.ProcessId != pid) throw new UnauthorizedAccessException();
            if (ancestor.Current.NativeWindowHandle == hwnd) { foundRoot = true; break; }
            ancestor = walker.GetParent(ancestor);
        }
        if (!foundRoot || !Security(hwnd, pid) || GetForegroundWindow().ToInt64() != hwnd) throw new UnauthorizedAccessException();
        if (!Preconditions(approved.ContainsKey("preconditions") ? approved["preconditions"] : null, pid, hwnd)) throw new UnauthorizedAccessException();
        RequireForeground(hwnd, pid);
        switch (action) {
            case "focus": RequireForeground(hwnd, pid); element.SetFocus(); break;
            case "invoke": var invoke = (InvokePattern)element.GetCurrentPattern(InvokePattern.Pattern); RequireForeground(hwnd, pid); invoke.Invoke(); break;
            case "toggle":
                var toggle = (TogglePattern)element.GetCurrentPattern(TogglePattern.Pattern);
                if (toggle.Current.ToggleState == ToggleState.Indeterminate || !(value is bool)) throw new UnauthorizedAccessException();
                if ((toggle.Current.ToggleState == ToggleState.On) != (bool)value) { RequireForeground(hwnd, pid); toggle.Toggle(); } break;
            case "select":
                var selection = (SelectionItemPattern)element.GetCurrentPattern(SelectionItemPattern.Pattern);
                if (selection.Current.SelectionContainer == null || !Same(String.Join(".", selection.Current.SelectionContainer.GetRuntimeId()), oldState["selection_container"])) throw new UnauthorizedAccessException();
                if (!selection.Current.IsSelected) { RequireForeground(hwnd, pid); selection.Select(); } break;
            case "expand": case "collapse":
                var expand = (ExpandCollapsePattern)element.GetCurrentPattern(ExpandCollapsePattern.Pattern);
                RequireForeground(hwnd, pid); if (action == "expand") expand.Expand(); else expand.Collapse(); break;
            case "set_value":
                var edit = (ValuePattern)element.GetCurrentPattern(ValuePattern.Pattern);
                if (edit.Current.IsReadOnly || edit.Current.Value.Length > 4096 || !(value is string) || ((string)value).Length > 4096) throw new UnauthorizedAccessException();
                RequireForeground(hwnd, pid); edit.SetValue((string)value); break;
            case "set_range_value":
                var range = (RangeValuePattern)element.GetCurrentPattern(RangeValuePattern.Pattern);
                double number = Convert.ToDouble(value);
                if (range.Current.IsReadOnly || Double.IsNaN(number) || Double.IsInfinity(number) || number < range.Current.Minimum || number > range.Current.Maximum) throw new UnauthorizedAccessException();
                RequireForeground(hwnd, pid); range.SetValue(number); break;
            case "scroll":
                var scroll = (ScrollPattern)element.GetCurrentPattern(ScrollPattern.Pattern);
                var v = (Dictionary<string, object>)value; double h = Convert.ToDouble(v["horizontal"]), vert = Convert.ToDouble(v["vertical"]);
                if (h < 0 || h > 100 || vert < 0 || vert > 100) throw new UnauthorizedAccessException();
                h = scroll.Current.HorizontallyScrollable ? h : ScrollPattern.NoScroll;
                vert = scroll.Current.VerticallyScrollable ? vert : ScrollPattern.NoScroll;
                RequireForeground(hwnd, pid); scroll.SetScrollPercent(h, vert); break;
            case "scroll_into_view": var item = (ScrollItemPattern)element.GetCurrentPattern(ScrollItemPattern.Pattern); RequireForeground(hwnd, pid); item.ScrollIntoView(); break;
            default: throw new UnauthorizedAccessException();
        }
        return new Dictionary<string, object> {{"sent", true}};
    }
}
'@
try {
    Add-Type -TypeDefinition $code -ReferencedAssemblies UIAutomationClient, UIAutomationTypes, WindowsBase, System.Web.Extensions, System, System.Core
    $line = [Console]::In.ReadLine()
    if (!$line -or $line.Length -gt 65536) { throw 'Invalid request' }
    $request = $line | ConvertFrom-Json
    if ($request.operation -eq 'capture') {
        $result = [SnapFlowActions]::Capture([long]$request.hwnd, [int]$request.pid)
    } elseif ($request.operation -eq 'execute') {
        $result = [SnapFlowActions]::Execute([long]$request.hwnd, [int]$request.pid, [string]$request.action, $line)
    } else { throw 'Invalid operation' }
    $result | ConvertTo-Json -Depth 16 -Compress
} catch {
    # Never emit native exception text or provider values.
    $nativeError = $_.Exception
    while ($nativeError.InnerException) { $nativeError = $nativeError.InnerException }
    @{error='native_action_unavailable'; error_type=$nativeError.GetType().Name} | ConvertTo-Json -Compress
    exit 1
}
