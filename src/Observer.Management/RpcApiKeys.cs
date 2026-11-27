using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Antiforgery;

record RpcKeyRequest(string Name, string Tier, DateTimeOffset? ExpiresAt);
record RpcKeySummary(string Id, string Name, string Tier, bool Enabled, DateTimeOffset? ExpiresAt);

sealed class RpcApiKeys(HttpClient client, string vault, Func<Task<string>> token) {
    readonly string root = Regex.IsMatch(vault, @"\A[a-z0-9-]{3,24}\z") ? $"https://{vault}.vault.azure.net/" :
        throw new ArgumentException("Invalid RPC key vault");
    readonly SemaphoreSlim createLock = new(1, 1);
    public static bool Valid(RpcKeyRequest r, DateTimeOffset now) =>
        r.Name is not null && Regex.IsMatch(r.Name, @"\A[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}\z") &&
        r.Tier is "partner" or "internal" && (r.ExpiresAt is null || r.ExpiresAt > now.AddMinutes(1));
    public static bool ValidId(string id) => Regex.IsMatch(id, @"\A[a-f0-9]{32}\z");
    async Task<JsonNode> Send(HttpMethod method, string url, object? body = null) {
        if (!url.StartsWith(root, StringComparison.Ordinal)) throw new InvalidOperationException("Unexpected vault URL");
        using var request = new HttpRequestMessage(method, url);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", await token());
        if (body is not null) request.Content = JsonContent.Create(body);
        using var response = await client.SendAsync(request);
        response.EnsureSuccessStatusCode();
        return JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
    }
    public async Task<List<RpcKeySummary>> List() {
        var result = new List<RpcKeySummary>();
        string? url = root + "secrets?api-version=7.4";
        while (url is not null) {
            var page = await Send(HttpMethod.Get, url);
            foreach (var item in page["value"]!.AsArray()) {
                var name = item!["id"]!.GetValue<string>().Split('/').Last();
                if (!name.StartsWith("rpc-key-", StringComparison.Ordinal) || !ValidId(name[8..])) continue;
                var exp = item["attributes"]?["exp"]?.GetValue<long>();
                result.Add(new(name[8..], item["tags"]?["name"]?.GetValue<string>() ?? name,
                    item["tags"]?["tier"]?.GetValue<string>() ?? "unknown",
                    item["attributes"]?["enabled"]?.GetValue<bool>() ?? true,
                    exp is null ? null : DateTimeOffset.FromUnixTimeSeconds(exp.Value)));
                if (result.Count > 10000) throw new InvalidOperationException("Too many retained RPC keys");
            }
            url = page["nextLink"]?.GetValue<string>();
        }
        return result;
    }
    public async Task<object> Create(RpcKeyRequest request) {
        if (!Valid(request, DateTimeOffset.UtcNow)) throw new ArgumentException("Invalid RPC key request");
        await createLock.WaitAsync();
        try {
            if ((await List()).Count(k => k.Enabled && (k.ExpiresAt is null || k.ExpiresAt > DateTimeOffset.UtcNow)) >= 100)
                throw new ArgumentException("Maximum 100 active RPC keys");
            var id = Guid.NewGuid().ToString("N");
            var key = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
            var attributes = new Dictionary<string, object> { ["enabled"] = true };
            if (request.ExpiresAt is not null) attributes["exp"] = request.ExpiresAt.Value.ToUnixTimeSeconds();
            await Send(HttpMethod.Put, root + $"secrets/rpc-key-{id}?api-version=7.4", new {
                value = JsonSerializer.Serialize(new { key, tier = request.Tier }),
                contentType = "application/json", attributes,
                tags = new Dictionary<string, string> { ["name"] = request.Name, ["tier"] = request.Tier, ["managed-by"] = "oprail-rpc" }
            });
            return new { id, key, name = request.Name, tier = request.Tier, expiresAt = request.ExpiresAt };
        } finally { createLock.Release(); }
    }
    public async Task Revoke(string id) {
        if (!ValidId(id)) throw new ArgumentException("Invalid RPC key ID");
        // Version metadata can be read even after a key is expired or disabled.
        string? url = root + $"secrets/rpc-key-{id}/versions?api-version=7.4";
        while (url is not null) {
            var page = await Send(HttpMethod.Get, url);
            foreach (var version in page["value"]!.AsArray()) {
                var versioned = version!["id"]!.GetValue<string>();
                if (!versioned.StartsWith(root + $"secrets/rpc-key-{id}/", StringComparison.Ordinal))
                    throw new InvalidOperationException("Unexpected secret version");
                await Send(HttpMethod.Patch, versioned + "?api-version=7.4", new { attributes = new { enabled = false } });
            }
            url = page["nextLink"]?.GetValue<string>();
        }
    }
    public static async Task<string> IdentityToken(HttpClient client, string resource = "https://vault.azure.net") {
        var endpoint = Environment.GetEnvironmentVariable("IDENTITY_ENDPOINT") ?? throw new InvalidOperationException("Managed identity unavailable");
        var header = Environment.GetEnvironmentVariable("IDENTITY_HEADER") ?? throw new InvalidOperationException("Managed identity unavailable");
        using var request = new HttpRequestMessage(HttpMethod.Get,
            endpoint + (endpoint.Contains('?') ? "&" : "?") + "api-version=2019-08-01&resource=" + Uri.EscapeDataString(resource));
        request.Headers.Add("X-IDENTITY-HEADER", header);
        using var response = await client.SendAsync(request);
        response.EnsureSuccessStatusCode();
        return JsonNode.Parse(await response.Content.ReadAsStringAsync())!["access_token"]!.GetValue<string>();
    }
}

static class RpcKeyEndpoints {
    public static void Map(RouteGroupBuilder secured, IConfiguration config) {
        var defaults = new Dictionary<string, (int Rate, int Burst)> {
            ["anonymous"] = (30, 60), ["partner"] = (100, 200), ["internal"] = (300, 600), ["backend"] = (500, 400) };
        var limits = defaults.ToDictionary(p => p.Key, p => new {
            rate = config.GetValue<int?>("RpcKeys:Limits:"+p.Key+":Rate") ?? p.Value.Rate,
            burst = config.GetValue<int?>("RpcKeys:Limits:"+p.Key+":Burst") ?? p.Value.Burst });
        secured.MapGet("/api/rpc-keys", async (HttpContext c) => {
            var keys = c.RequestServices.GetService<RpcApiKeys>();
            c.Response.Headers.CacheControl = "no-store";
            if (keys is null) return Results.Ok(new { configured = false, keys = Array.Empty<object>() });
            return Results.Ok(new { configured = true, active = config.GetValue<bool>("RpcKeys:ProxyEnabled"), keys = await keys.List(), limits });
        });
        secured.MapPost("/api/rpc-keys", async (HttpContext c, IAntiforgery csrf, RpcKeyRequest request) => {
            var keys = c.RequestServices.GetService<RpcApiKeys>();
            c.Response.Headers.CacheControl = "no-store";
            try { await csrf.ValidateRequestAsync(c); }
            catch (AntiforgeryValidationException) { return Results.BadRequest(new { error = "Refresh the management page and try again." }); }
            if (keys is null) return Results.Conflict(new { error = "RPC key management is not configured." });
            if (!config.GetValue<bool>("RpcKeys:ProxyEnabled"))
                return Results.Conflict(new { error = "API-key tiers are not activated on the RPC proxies. The running observer is unchanged." });
            if (!RpcApiKeys.Valid(request, DateTimeOffset.UtcNow))
                return Results.BadRequest(new { error = "Use a name of 1–80 letters, digits, spaces, dots, hyphens or underscores, a valid tier, and a future expiration." });
            try {
                var created = await keys.Create(request);
                c.RequestServices.GetRequiredService<ILogger<RpcApiKeys>>().LogInformation("RPC key created: {Name}, tier {Tier}, by {User}", request.Name, request.Tier, c.User.Identity?.Name);
                return Results.Ok(created);
            }
            catch (ArgumentException) { return Results.BadRequest(new { error = "RPC key limit reached or invalid request." }); }
        });
        secured.MapDelete("/api/rpc-keys/{id}", async (string id, HttpContext c, IAntiforgery csrf) => {
            var keys = c.RequestServices.GetService<RpcApiKeys>();
            c.Response.Headers.CacheControl = "no-store";
            try { await csrf.ValidateRequestAsync(c); }
            catch (AntiforgeryValidationException) { return Results.BadRequest(new { error = "Refresh the management page and try again." }); }
            if (keys is null) return Results.Conflict(new { error = "RPC key management is not configured." });
            if (!RpcApiKeys.ValidId(id)) return Results.BadRequest(new { error = "Invalid RPC key ID." });
            await keys.Revoke(id);
            c.RequestServices.GetRequiredService<ILogger<RpcApiKeys>>().LogInformation("RPC key revoked: {KeyId}, by {User}", id, c.User.Identity?.Name);
            return Results.Ok(new { revoked = true });
        });
    }
}
