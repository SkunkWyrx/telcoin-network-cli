using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

static class RpcApiKeyTests {
    static void Check(bool ok, string label) { if (!ok) throw new Exception(label); }
    sealed class Vault : HttpMessageHandler {
        readonly List<JsonNode> metadata = [];
        public readonly List<(string Method, string Path, string? Body)> Requests = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken cancellation) {
            var body = r.Content is null ? null : await r.Content.ReadAsStringAsync(cancellation);
            Requests.Add((r.Method.Method, r.RequestUri!.AbsolutePath, body));
            if (r.Method == HttpMethod.Put) {
                var value = JsonNode.Parse(body!)!;
                metadata.Add(new JsonObject { ["id"] = r.RequestUri.GetLeftPart(UriPartial.Path),
                    ["attributes"] = value["attributes"]!.DeepClone(), ["tags"] = value["tags"]!.DeepClone() });
            }
            object response = r.Method == HttpMethod.Get && r.RequestUri.AbsolutePath.EndsWith("/versions") ?
                new { value = new[] { new { id = "https://test-vault.vault.azure.net" + r.RequestUri.AbsolutePath[..^9] + "/version" } } } :
                r.Method == HttpMethod.Get ? new { value = metadata } : new { id = "created" };
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(response)) };
        }
    }
    public static async Task Run() {
        var now = DateTimeOffset.UtcNow;
        Check(RpcApiKeys.Valid(new("App 1", "partner", now.AddDays(1)), now), "Valid key request");
        Check(!RpcApiKeys.Valid(new("App", "unlimited", null), now), "Unknown tier denied");
        Check(!RpcApiKeys.Valid(new("App", "partner", now.AddSeconds(-1)), now), "Expired request denied");
        Check(!RpcApiKeys.Valid(new("<script>", "internal", null), now), "Invalid name denied");
        Check(!RpcApiKeys.ValidId("../other-secret"), "Secret path injection denied");
        using var vault = new Vault();
        using var client = new HttpClient(vault);
        var store = new RpcApiKeys(client, "test-vault", () => Task.FromResult("test-token"));
        var created = JsonSerializer.SerializeToNode(await store.Create(new("App", "partner", now.AddDays(1))))!;
        var key = created["key"]!.GetValue<string>();
        var id = created["id"]!.GetValue<string>();
        Check(key.Length == 64 && key.All(c => "0123456789abcdef".Contains(c)), "256-bit random key returned only at creation");
        Check(RpcApiKeys.ValidId(id), "Opaque key ID");
        var put = vault.Requests.Single(r => r.Method == "PUT");
        var payload = JsonNode.Parse(put.Body!)!;
        Check(JsonNode.Parse(payload["value"]!.GetValue<string>())!["key"]!.GetValue<string>() == key, "Key stored in vault");
        Check(payload["attributes"]!["exp"]!.GetValue<long>() == now.AddDays(1).ToUnixTimeSeconds(), "Expiry stored as vault attribute");
        vault.Requests.Clear();
        await store.List();
        Check(vault.Requests.Single().Path == "/secrets", "Listing reads metadata, never secret values");
        vault.Requests.Clear();
        await store.Revoke(id);
        Check(vault.Requests[0].Path.EndsWith("/versions"), "Revocation reads only version metadata");
        Check(vault.Requests[1].Method == "PATCH" && JsonNode.Parse(vault.Requests[1].Body!)!["attributes"]!["enabled"]!.GetValue<bool>() == false,
              "Revocation disables the exact key version");
        var builder = WebApplication.CreateBuilder();
        builder.WebHost.UseSetting("urls", "http://127.0.0.1:0");
        builder.Services.AddAntiforgery(o => o.HeaderName = "X-CSRF-TOKEN");
        builder.Services.AddSingleton(store);
        builder.Configuration["RpcKeys:ProxyEnabled"] = "true";
        await using var app = builder.Build();
        RpcKeyEndpoints.Map(app.MapGroup(""), builder.Configuration);
        app.MapGet("/test-csrf", (HttpContext c, IAntiforgery csrf) => Results.Ok(new { token = csrf.GetAndStoreTokens(c).RequestToken }));
        await app.StartAsync();
        try {
            using var http = new HttpClient(new HttpClientHandler { CookieContainer = new CookieContainer() }) { BaseAddress = new Uri(app.Urls.Single()) };
            using var list = await http.GetAsync("/api/rpc-keys");
            Check(list.IsSuccessStatusCode && list.Headers.CacheControl?.NoStore == true, "Key list is not cacheable");
            Check(!(await list.Content.ReadAsStringAsync()).Contains(key), "List does not reveal key material");
            using var csrfReply = await http.GetAsync("/test-csrf");
            var csrf = JsonNode.Parse(await csrfReply.Content.ReadAsStringAsync())!["token"]!.GetValue<string>();
            var request = new HttpRequestMessage(HttpMethod.Post, "/api/rpc-keys") {
                Content = new StringContent("{\"name\":\"HTTP app\",\"tier\":\"internal\",\"expiresAt\":null}", System.Text.Encoding.UTF8, "application/json") };
            request.Headers.Add("X-CSRF-TOKEN", csrf);
            using var response = await http.SendAsync(request);
            Check(response.IsSuccessStatusCode && response.Headers.CacheControl?.NoStore == true, "Create endpoint returns uncached one-time credential");
            builder.Configuration["RpcKeys:ProxyEnabled"] = "false";
            var before = vault.Requests.Count(r => r.Method == "PUT");
            using var inactiveList = await http.GetAsync("/api/rpc-keys");
            Check(!JsonNode.Parse(await inactiveList.Content.ReadAsStringAsync())!["active"]!.GetValue<bool>(), "Website-only release reports proxies inactive");
            using var staged = new HttpRequestMessage(HttpMethod.Post, "/api/rpc-keys") {
                Content = new StringContent("{\"name\":\"Staged app\",\"tier\":\"partner\"}", System.Text.Encoding.UTF8, "application/json") };
            staged.Headers.Add("X-CSRF-TOKEN", csrf);
            using var blocked = await http.SendAsync(staged);
            Check(blocked.StatusCode == HttpStatusCode.Conflict && vault.Requests.Count(r => r.Method == "PUT") == before,
                  "Inactive proxies block key creation without writing a secret");
            using var denied = await http.PostAsync("/api/rpc-keys", new StringContent("{\"name\":\"App\",\"tier\":\"partner\"}", System.Text.Encoding.UTF8, "application/json"));
            Check(denied.StatusCode == HttpStatusCode.BadRequest, "Mutation without CSRF denied");
            using var noReveal = await http.GetAsync("/api/rpc-keys/"+id);
            Check(noReveal.StatusCode == HttpStatusCode.MethodNotAllowed, "No endpoint can retrieve an existing key");
        } finally { await app.StopAsync(); }
        var disabledBuilder = WebApplication.CreateBuilder();
        disabledBuilder.WebHost.UseSetting("urls", "http://127.0.0.1:0");
        disabledBuilder.Services.AddAntiforgery();
        disabledBuilder.Services.AddSingleton<RpcApiKeys>(_ => null!);
        await using var disabled = disabledBuilder.Build();
        RpcKeyEndpoints.Map(disabled.MapGroup(""), disabledBuilder.Configuration);
        await disabled.StartAsync();
        try {
            using var http = new HttpClient { BaseAddress = new Uri(disabled.Urls.Single()) };
            using var response = await http.GetAsync("/api/rpc-keys");
            var body = JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
            Check(response.IsSuccessStatusCode && !body["configured"]!.GetValue<bool>(), "Unconfigured vault disables key UI without breaking the dashboard");
        } finally { await disabled.StopAsync(); }
        Console.WriteLine("RPC key validation, vault storage, metadata-only listing, revocation, CSRF and no-store HTTP checks passed.");
    }
}
