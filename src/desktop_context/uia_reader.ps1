param([long]$TargetHwnd, [int]$TargetPid, [int]$IncludeUI, [int]$IncludeSelection, [int]$ObserveOnly = 0)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, WindowsBase
$code = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.RegularExpressions;
using System.Windows.Automation;
public static class SnapFlowContextReader {
    public static void Observe() {
        var throttle = Stopwatch.StartNew();
        object gate = new object();
        AutomationFocusChangedEventHandler handler = delegate(object sender, AutomationFocusChangedEventArgs args) {
            lock(gate) {
                if (throttle.ElapsedMilliseconds < 500) return;
                // No element properties, text, process names or IDs are read here.
                Console.WriteLine("changed"); Console.Out.Flush(); throttle.Restart();
            }
        };
        try {
            Automation.AddAutomationFocusChangedEventHandler(handler);
            while (true) System.Threading.Thread.Sleep(1000);
        } finally { Automation.RemoveAutomationFocusChangedEventHandler(handler); }
    }
    static Stopwatch timer;
    static List<Dictionary<string, object>> elements;
    static HashSet<string> seen;
    static bool limited;
    static int remaining;
    static bool captureUI;
    static TreeWalker walker = TreeWalker.ControlViewWalker;
    static Regex credentials = new Regex(@"password|passphrase|credential|api[ _-]?key|access[ _-]?token|secret|pin\b|one[ -]?time[ -]?code", RegexOptions.IgnoreCase);
    static string Bound(string value, int max) {
        if (String.IsNullOrEmpty(value)) return null;
        int count = Math.Min(Math.Min(value.Length, max), remaining);
        remaining -= count;
        return value.Substring(0, count);
    }
    static bool Budget() { return elements.Count < 300 && timer.ElapsedMilliseconds < 2200 && remaining > 0; }
    static string Id(AutomationElement e) { return String.Join(".", e.GetRuntimeId()); }
    static bool Protected(AutomationElement e) {
        // IsPassword is tested before reading any names, values or text patterns.
        if (e.Current.IsPassword) return true;
        return credentials.IsMatch(e.Current.Name ?? "") || credentials.IsMatch(e.Current.AutomationId ?? "");
    }
    static void Read(AutomationElement e, int depth) {
        if (e == null || !Budget()) return;
        try {
            string id = Id(e);
            if (!seen.Add(id)) return;
            if (Protected(e) || e.Current.IsOffscreen) return;
            if (!captureUI) {
                elements.Add(new Dictionary<string, object> {{"element_id", id}, {"focused", true}, {"depth", depth}});
                return;
            }
            var c = e.Current;
            string value = null;
            object pattern;
            if (c.HasKeyboardFocus && e.TryGetCurrentPattern(ValuePattern.Pattern, out pattern)) {
                var v = ((ValuePattern)pattern).Current;
                value = Bound(v.Value, 1500);
            }
            var r = c.BoundingRectangle;
            elements.Add(new Dictionary<string, object> {
                {"element_id", id}, {"control_type", c.ControlType.ProgrammaticName},
                {"name", Bound(c.Name, 500)}, {"automation_id", Bound(c.AutomationId, 128)},
                {"class_name", Bound(c.ClassName, 128)}, {"value", value},
                {"enabled", c.IsEnabled}, {"focused", c.HasKeyboardFocus},
                {"bounds", new double[] {r.Left, r.Top, r.Right, r.Bottom}}, {"depth", depth}
            });
        } catch { limited = true; }
    }
    static void Walk(AutomationElement e, int depth) {
        if (e == null || depth > 6 || !Budget()) return;
        try {
            if (Protected(e) || e.Current.IsOffscreen) return;
            Read(e, depth);
            if (depth == 6) return;
            var child = walker.GetFirstChild(e);
            int siblings = 0;
            while (child != null && Budget() && siblings++ < 300) {
                Walk(child, depth + 1);
                child = walker.GetNextSibling(child);
            }
        } catch { limited = true; }
    }
    public static Dictionary<string, object> Capture(long hwnd, int pid, bool ui, bool selection) {
        timer = Stopwatch.StartNew(); elements = new List<Dictionary<string, object>>();
        seen = new HashSet<string>(); remaining = 9000; limited = false; captureUI = ui;
        string focusedId = null, selected = null; bool protectedFocus = false;
        try {
            var root = AutomationElement.FromHandle(new IntPtr(hwnd));
            if (root.Current.ProcessId != pid) throw new InvalidOperationException();
            var focus = AutomationElement.FocusedElement;
            // Verify HWND ancestry, not merely matching a multi-window process ID.
            bool inWindow = false;
            var node = focus;
            var ancestors = new List<AutomationElement>();
            for (int i = 0; node != null && i < 32 && timer.ElapsedMilliseconds < 2200; i++) {
                if (node.Equals(root)) { ancestors.Add(root); inWindow = true; break; }
                ancestors.Add(node);
                node = walker.GetParent(node);
            }
            bool safeFocus = inWindow && focus != null && !Protected(focus);
            // Also exclude descendants of password/credential controls.
            if (safeFocus) foreach (var parent in ancestors) if (Protected(parent)) safeFocus = false;
            protectedFocus = inWindow && focus != null && !safeFocus;
            if (safeFocus) {
                focusedId = Id(focus);
                Read(focus, 0);
                if (selection) {
                    object pattern;
                    if (focus.TryGetCurrentPattern(TextPattern.Pattern, out pattern)) {
                        var ranges = ((TextPattern)pattern).GetSelection();
                        var parts = new List<string>(); int chars = 0;
                        foreach (var range in ranges) {
                            if (chars >= 3000 || timer.ElapsedMilliseconds >= 2200) break;
                            string text = range.GetText(3000 - chars);
                            if (!String.IsNullOrEmpty(text)) { parts.Add(text); chars += text.Length; }
                        }
                        selected = String.Join("\n", parts);
                    }
                }
                if (ui) {
                    for (int i = 1; i < Math.Min(ancestors.Count, 6); i++) Read(ancestors[i], i);
                    var parent = walker.GetParent(focus);
                    var sibling = parent == null ? null : walker.GetFirstChild(parent);
                    int count = 0;
                    while (sibling != null && Budget() && count++ < 20) {
                        Read(sibling, 1); sibling = walker.GetNextSibling(sibling);
                    }
                }
            }
            if (ui) Walk(root, 0);
            if (!Budget()) limited = true;
        } catch { limited = true; }
        return new Dictionary<string, object> {{"elements", elements}, {"focused_id", focusedId},
            {"selected_text", selected}, {"limited", limited}, {"protected_focus", protectedFocus}};
    }
}
'@
$references = @([System.Windows.Automation.AutomationElement].Assembly.Location,
    [System.Windows.Automation.ControlType].Assembly.Location, [System.Windows.Rect].Assembly.Location)
Add-Type -TypeDefinition $code -ReferencedAssemblies $references
if ($ObserveOnly -eq 1) { [SnapFlowContextReader]::Observe(); exit }
[SnapFlowContextReader]::Capture($TargetHwnd, $TargetPid, ($IncludeUI -eq 1), ($IncludeSelection -eq 1)) | ConvertTo-Json -Depth 8 -Compress
