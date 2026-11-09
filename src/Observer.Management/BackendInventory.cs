using System.Text.Json.Nodes;

static class BackendInventory {
    public static IReadOnlyCollection<string> NicConfigurationIds(JsonNode pool) {
        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in pool["properties"]?["backendIPConfigurations"]?.AsArray() ?? [])
            ids.Add(entry?["id"]?.GetValue<string>() ?? throw new InvalidOperationException("Backend NIC reference is missing"));
        // Azure can expose NIC-backed members in both collections. The second
        // collection alone does not mean the pool uses IP-only membership.
        foreach (var entry in pool["properties"]?["loadBalancerBackendAddresses"]?.AsArray() ?? []) {
            var id = entry?["properties"]?["networkInterfaceIPConfiguration"]?["id"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(id))
                throw new InvalidOperationException("IP-based backends require explicit VM identity mapping; refusing incomplete inventory");
            ids.Add(id);
        }
        return ids;
    }
}
