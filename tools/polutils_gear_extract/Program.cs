using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using PlayOnline.FFXI;
using PlayOnline.FFXI.Things;

namespace PolUtilsGearExtractor
{
    internal static class Program
    {
        private static readonly int[] ItemFiles = { 73, 74, 75, 76, 55668, 77 };

        private static void Usage()
        {
            Console.Error.WriteLine("Usage: PolUtilsGearExtractor.exe --ffxi-root <FINAL FANTASY XI> --output <catalog.json>");
        }

        private static string Argument(string[] args, string name)
        {
            for (var i = 0; i + 1 < args.Length; i++)
            {
                if (String.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
                    return args[i + 1];
            }
            return null;
        }

        private static string ResolveFile(string root, int fileNumber)
        {
            for (byte table = 1; table < 20; table++)
            {
                var suffix = table == 1 ? String.Empty : table.ToString();
                var tableRoot = table == 1 ? root : Path.Combine(root, "Rom" + suffix);
                var vtablePath = Path.Combine(tableRoot, "VTABLE" + suffix + ".DAT");
                var ftablePath = Path.Combine(tableRoot, "FTABLE" + suffix + ".DAT");
                if (!File.Exists(vtablePath) || !File.Exists(ftablePath))
                    continue;

                var vtable = File.ReadAllBytes(vtablePath);
                if (fileNumber >= vtable.Length || vtable[fileNumber] != table)
                    continue;

                var ftable = File.ReadAllBytes(ftablePath);
                var offset = fileNumber * 2;
                if (offset + 1 >= ftable.Length)
                    continue;

                var fileDirectory = (ushort)(ftable[offset] | (ftable[offset + 1] << 8));
                var directory = fileDirectory / 0x80;
                var file = fileDirectory % 0x80;
                var romDirectory = table == 1 ? "Rom" : "Rom" + table;
                return Path.Combine(root, romDirectory, directory.ToString(), file + ".dat");
            }

            return null;
        }

        private static string Field(Item item, string name)
        {
            if (!item.HasField(name))
                return null;

            // POLUtils displays many numeric values as "HEX (decimal)".
            // Export the decimal portion so the downstream JSON converter
            // does not have to understand presentation-oriented field text.
            var text = item.GetFieldText(name);
            var open = text.IndexOf('(');
            var close = text.IndexOf(')', open + 1);
            if (open >= 0 && close > open)
                return text.Substring(open + 1, close - open - 1).Trim();
            return text;
        }

        private static string NumericField(Item item, string name)
        {
            if (!item.HasField(name))
                return null;

            var value = item.GetFieldValue(name);
            if (value != null)
            {
                try
                {
                    return Convert.ToUInt64(value, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
                }
                catch (FormatException) { }
                catch (InvalidCastException) { }
                catch (OverflowException) { }
            }

            return Field(item, name);
        }

        private static Dictionary<string, object> ExportItem(Item item, int sourceFile)
        {
            var name = Field(item, "name");
            if (String.IsNullOrWhiteSpace(name) || name == ".")
                return null;

            var result = new Dictionary<string, object>();
            result["item_id"] = NumericField(item, "id");
            result["name"] = name;
            result["description"] = Field(item, "description") ?? String.Empty;
            result["level"] = NumericField(item, "level") ?? "0";
            result["item_level"] = NumericField(item, "iLevel") ?? "0";
            result["slots_mask"] = NumericField(item, "slots") ?? "0";
            result["races_mask"] = NumericField(item, "races") ?? "0";
            result["jobs_mask"] = NumericField(item, "jobs") ?? "0";
            result["damage"] = NumericField(item, "damage") ?? "0";
            result["delay"] = NumericField(item, "delay") ?? "0";
            result["skill"] = NumericField(item, "skill") ?? "0";
            result["type"] = NumericField(item, "type") ?? "0";
            result["flags"] = NumericField(item, "flags") ?? "0";
            result["source_file"] = sourceFile;
            return result;
        }

        private static int Main(string[] args)
        {
            var root = Argument(args, "--ffxi-root");
            var output = Argument(args, "--output");
            if (String.IsNullOrWhiteSpace(root) || String.IsNullOrWhiteSpace(output))
            {
                Usage();
                return 2;
            }

            root = Path.GetFullPath(root);
            output = Path.GetFullPath(output);
            if (!Directory.Exists(root))
            {
                Console.Error.WriteLine("FFXI root does not exist: " + root);
                return 3;
            }

            var items = new Dictionary<int, Dictionary<string, object>>();
            foreach (var fileNumber in ItemFiles)
            {
                var path = ResolveFile(root, fileNumber);
                if (path == null || !File.Exists(path))
                {
                    Console.Error.WriteLine("Could not resolve item DAT " + fileNumber);
                    return 4;
                }

                Console.WriteLine("Reading " + fileNumber + ": " + path);
                var list = FileType.LoadAll(path, null);
                if (list == null)
                {
                    Console.Error.WriteLine("POLUtils could not parse " + path);
                    return 5;
                }

                foreach (var thing in list)
                {
                    var item = thing as Item;
                    if (item == null)
                        continue;
                    var exported = ExportItem(item, fileNumber);
                    if (exported == null)
                        continue;
                    uint id;
                    var idText = (string)exported["item_id"];
                    if (!UInt32.TryParse(idText, NumberStyles.Integer, CultureInfo.InvariantCulture, out id)
                        && !UInt32.TryParse(idText, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out id))
                        continue;
                    items[(int)id] = exported;
                }
                Console.WriteLine("  items: " + list.Count);
            }

            var document = new Dictionary<string, object>
            {
                ["source"] = "POLUtils native FFXI DAT resources",
                ["source_root"] = root,
                ["generated_utc"] = DateTime.UtcNow.ToString("o"),
                ["item_files"] = ItemFiles,
                ["items"] = new List<Dictionary<string, object>>(items.Values),
            };

            var directoryName = Path.GetDirectoryName(output);
            if (!String.IsNullOrEmpty(directoryName))
                Directory.CreateDirectory(directoryName);
            var serializer = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue };
            File.WriteAllText(output, serializer.Serialize(document), new UTF8Encoding(false));
            Console.WriteLine("Wrote " + items.Count + " items to " + output);
            return 0;
        }
    }
}
