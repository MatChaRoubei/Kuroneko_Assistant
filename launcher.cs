using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyVersion("0.0.9.0")]
[assembly: AssemblyFileVersion("0.0.9.0")]
[assembly: AssemblyTitle("黑猫语音助手 · 最新版入口")]

class Launcher {
    [STAThread]
    static void Main(string[] args) {
        string executable = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "release-0.0.9", "VoiceAssistant", "VoiceAssistant.exe");
        if (!File.Exists(executable)) {
            MessageBox.Show("没有找到新版程序。请解压完整分享包，或从 release-0.0.9\\VoiceAssistant 文件夹启动。", "黑猫语音助手");
            return;
        }
        string switches = "";
        foreach (string arg in args) {
            if (arg == "--debug" || arg == "--no-file" || arg == "--text-only" || arg == "--check-voice") switches += arg + " ";
        }
        Process.Start(new ProcessStartInfo(executable, switches) {
            WorkingDirectory = Path.GetDirectoryName(executable), UseShellExecute = false
        });
    }
}
