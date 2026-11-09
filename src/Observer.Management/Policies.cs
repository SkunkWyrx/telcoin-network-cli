using System.Text.RegularExpressions;
using System.Text.Json.Nodes;

record Operation(string Action, string Argument) {
    public static bool Valid(Operation o) => o.Argument is not null && o.Argument.Length <= 300 && o.Action switch {
        "add" => Regex.IsMatch(o.Argument, @"\A[1-9][0-9]?\z"),
        "remove" or "snapshot" => Regex.IsMatch(o.Argument, @"\A[A-Za-z0-9][A-Za-z0-9-]{0,63}\z"),
        "build" => Regex.IsMatch(o.Argument, @"\Arefs/(tags|heads)/[A-Za-z0-9._/-]+\z"),
        "upgrade-tag" => Regex.IsMatch(o.Argument, @"\Av[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9._-]+)?\z"),
        "upgrade" => Regex.IsMatch(o.Argument, @"\A[a-z0-9./-]+@sha256:[a-f0-9]{64}\z"),
        _ => false
    };
}

static class AccessPolicy {
    public static bool IsOwner(string? expected, string? actual) =>
        Guid.TryParse(expected, out var owner) && Guid.TryParse(actual, out var user) && owner == user;

    public static bool Allows(JsonNode permissions, string action) =>
        permissions["value"]!.AsArray().Any(p =>
            p!["actions"]!.AsArray().Any(a => Matches(a!.GetValue<string>(), action)) &&
            !p["notActions"]!.AsArray().Any(a => Matches(a!.GetValue<string>(), action)));

    public static bool Matches(string pattern, string action) => Regex.IsMatch(action, "^" + Regex.Escape(pattern).Replace("\\*", ".*") + "$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
}
