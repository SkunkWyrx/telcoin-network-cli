using System.Globalization;
using System.Net.Http.Headers;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Antiforgery;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddHttpClient("remote", c => c.Timeout = TimeSpan.FromSeconds(15));
builder.Services.AddAntiforgery(o => o.HeaderName = "X-CSRF-TOKEN");
builder.Services.AddAuthentication(o => { o.DefaultScheme = "Cookies"; o.DefaultChallengeScheme = "Microsoft"; })
    .AddCookie("Cookies", o => { o.Cookie.SecurePolicy = CookieSecurePolicy.Always; o.ExpireTimeSpan = TimeSpan.FromHours(1); })
    .AddOpenIdConnect("Microsoft", o => {
        var tenant = builder.Configuration["Entra:TenantId"];
        if (!Guid.TryParse(tenant, out _)) throw new InvalidOperationException("Configure a single Entra tenant ID.");
        o.Authority = $"https://login.microsoftonline.com/{tenant}/v2.0";
        o.ClientId = builder.Configuration["Entra:ClientId"];
        o.ClientSecret = builder.Configuration["Entra:ClientSecret"];
        o.ResponseType = "code"; o.UsePkce = true; o.SaveTokens = true;
        o.Scope.Add("https://management.azure.com/user_impersonation");
        o.GetClaimsFromUserInfoEndpoint = false;
    });
builder.Services.AddAuthorization();
var app = builder.Build();
app.UseExceptionHandler(handler => handler.Run(async c => { c.Response.StatusCode = 502; await c.Response.WriteAsJsonAsync(new { error = "Azure or operation service unavailable. Check server logs; no operation was confirmed." }); }));
// Container Apps terminates TLS at its HTTPS-only ingress. Use the configured
// public origin for OIDC callbacks instead of trusting incoming forwarded headers.
if (builder.Configuration["Management:PublicHost"] is { Length: > 0 } publicHost)
    app.Use(async (context, next) => {
        context.Request.Scheme = "https";
        context.Request.Host = new HostString(publicHost);
        await next(context);
    });

app.UseHttpsRedirection(); app.UseAuthentication(); app.UseAuthorization();
app.MapGet("/healthz", () => Results.Ok(new { status = "running" }));
var secured = app.MapGroup("").RequireAuthorization();
secured.AddEndpointFilter(async (context, next) => {
    var h = context.HttpContext;
    var oid = h.User.FindFirst("http://schemas.microsoft.com/identity/claims/objectidentifier")?.Value ?? h.User.FindFirst("oid")?.Value;
    if (!AccessPolicy.IsOwner(builder.Configuration["Entra:AllowedObjectId"], oid)) return Results.Forbid();
    var expires = await h.GetTokenAsync("expires_at");
    if (!DateTimeOffset.TryParse(expires, out var expiry) || expiry <= DateTimeOffset.UtcNow.AddMinutes(1)) {
        await h.SignOutAsync("Cookies"); return Results.Challenge(authenticationSchemes: ["Microsoft"]);
    }
    var remote = new Remote(h, builder.Configuration, h.RequestServices.GetRequiredService<IHttpClientFactory>().CreateClient("remote"));
    var permissions = await remote.Arm("/providers/Microsoft.Authorization/permissions?api-version=2022-04-01");
    // Evaluate each permission block separately: NotActions is not a global deny.
    if (!AccessPolicy.Allows(permissions, "Microsoft.Compute/virtualMachines/write") || !AccessPolicy.Allows(permissions, "Microsoft.Compute/virtualMachines/delete") || !AccessPolicy.Allows(permissions, "Microsoft.Network/loadBalancers/write")) return Results.Forbid();
    h.Items["remote"] = remote;
    return await next(context);
});
secured.MapGet("/", () => Results.File(Path.Combine(app.Environment.WebRootPath, "index.html"), "text/html"));
secured.MapGet("/api/session", (HttpContext c, IAntiforgery csrf) => Results.Ok(new {
    user = c.User.Identity?.Name, network = builder.Configuration["Fleet:Network"],
    resourceGroup = builder.Configuration["Fleet:ResourceGroup"],
    operationsEnabled = builder.Configuration.GetValue<bool>("Operations:Enabled"),
    csrf = csrf.GetAndStoreTokens(c).RequestToken
}));
FleetEndpoints.MapStatus(secured, c => ((Remote)c.Items["remote"]!).Status());
secured.MapPost("/api/operations", async (HttpContext c, IAntiforgery csrf, Operation operation) => {
    await csrf.ValidateRequestAsync(c);
    if (!builder.Configuration.GetValue<bool>("Operations:Enabled")) return Results.Conflict(new { error = "Operations are disabled until the trusted runner is configured." });
    if (!Operation.Valid(operation)) return Results.BadRequest(new { error = "Choose an operation and provide a valid node, release tag, or image digest." });
    var remote = (Remote)c.Items["remote"]!;
    if (operation.Action == "remove") {
        var names = await remote.Members();
        if (names.Count < 2 || !names.ContainsKey(operation.Argument)) return Results.Conflict(new { error = "Cannot remove the final node or a node outside this pool." });
    }
    var requestId = Guid.NewGuid().ToString("D");
    var client = new OperationsClient(builder.Configuration, c.RequestServices.GetRequiredService<IHttpClientFactory>().CreateClient("remote"));
    await client.Dispatch(operation, requestId,
        c.User.FindFirst("http://schemas.microsoft.com/identity/claims/objectidentifier")?.Value ?? c.User.FindFirst("oid")!.Value);
    app.Logger.LogInformation("Observer request {RequestId}: {Action} {Argument} requested by {User}", requestId, operation.Action, operation.Argument, c.User.Identity?.Name);
    return Results.Accepted(value: new { requestId, message = "Submitted for environment approval. The operation will appear below after GitHub creates the run.", url = client.HistoryUrl });
});
secured.MapGet("/api/operations", async (HttpContext c) => {
    if (string.IsNullOrEmpty(builder.Configuration["Operations:GitHubToken"]))
        return Results.Ok(new { configured = false, runs = Array.Empty<object>() });
    var client = new OperationsClient(builder.Configuration, c.RequestServices.GetRequiredService<IHttpClientFactory>().CreateClient("remote"));
    return Results.Ok(new { configured = true, runs = await client.History() });
});
app.Run();


class Remote(HttpContext context, IConfiguration config, HttpClient client) {
    readonly string root = $"/subscriptions/{config["Fleet:Subscription"]}/resourceGroups/{config["Fleet:ResourceGroup"]}";
    public async Task<JsonNode> Arm(string suffix, bool absolute = false) {
        var request = new HttpRequestMessage(HttpMethod.Get, "https://management.azure.com" + (absolute ? suffix : root + suffix));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", await context.GetTokenAsync("access_token"));
        using var response = await client.SendAsync(request); response.EnsureSuccessStatusCode();
        return JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
    }
    public async Task<Dictionary<string,string>> Members(string? loadBalancer = null) {
        var lb = await Arm($"/providers/Microsoft.Network/loadBalancers/{loadBalancer ?? config["Fleet:LoadBalancer"]}?api-version=2024-05-01");
        var pool = lb["properties"]!["backendAddressPools"]!.AsArray().Single(p => p!["name"]!.GetValue<string>() == config["Fleet:BackendPool"]);
        var result = new Dictionary<string,string>();
        foreach (var id in BackendInventory.NicConfigurationIds(pool!)) {
            var nicId = id[..id.IndexOf("/ipConfigurations/", StringComparison.OrdinalIgnoreCase)];
            if (!nicId.StartsWith(root + "/providers/Microsoft.Network/networkInterfaces/", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Unexpected backend resource scope");
            var nic = await Arm(nicId + "?api-version=2024-05-01", true);
            var vmId = nic["properties"]?["virtualMachine"]?["id"]?.GetValue<string>();
            if (vmId is null || !vmId.StartsWith(root + "/providers/Microsoft.Compute/virtualMachines/", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Unexpected VM scope");
            var ip = nic["properties"]!["ipConfigurations"]!.AsArray().Single(i => string.Equals(i!["id"]!.GetValue<string>(), id, StringComparison.OrdinalIgnoreCase));
            var address = ip!["properties"]!["privateIPAddress"]!.GetValue<string>();
            if (!System.Net.IPAddress.TryParse(address, out var parsed) || parsed.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork) throw new InvalidOperationException("Expected IPv4 backend");
            result[vmId.Split('/').Last()] = address;
        }
        return result;
    }
    async Task<JsonNode> Rpc(string endpoint, string method, object[] args) {
        using var response = await client.PostAsJsonAsync(endpoint, new { jsonrpc = "2.0", id = 1, method, @params = args }); response.EnsureSuccessStatusCode();
        var body = JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
        return body["result"] ?? throw new InvalidOperationException("RPC returned no result");
    }
    async Task<bool?> TrafficReady(string address) {
        try {
            using var response = await client.GetAsync($"http://{address}:19091/readyz");
            return response.StatusCode switch { System.Net.HttpStatusCode.OK => true, System.Net.HttpStatusCode.ServiceUnavailable => false, _ => null };
        } catch { return null; }
    }
    static long Height(JsonNode value) => long.Parse(value.GetValue<string>()[2..], NumberStyles.HexNumber);
    public async Task<object> Status() {
        var reference = config["Fleet:ReferenceRpc"]!;
        var expectedChain = config["Fleet:ChainId"]!;
        var networkChain = await Rpc(reference, "eth_chainId", []);
        if (Height(networkChain) != Height(JsonValue.Create(expectedChain)!)) throw new InvalidOperationException("Reference chain mismatch");
        var head = Height(await Rpc(reference, "eth_blockNumber", []));
        var members = await Members();
        var publicMembers = string.IsNullOrEmpty(config["Fleet:PublicLoadBalancer"]) ? members :
            await Members(config["Fleet:PublicLoadBalancer"]);
        var nodes = await Task.WhenAll(members.Select(async member => {
            try {
                var vm = await Arm($"/providers/Microsoft.Compute/virtualMachines/{member.Key}/instanceView?api-version=2024-07-01");
                var power = vm["statuses"]!.AsArray().Select(s => s!["code"]!.GetValue<string>()).FirstOrDefault(s => s.StartsWith("PowerState/")) ?? "unknown";
                if (power != "PowerState/running") return (object)new { name = member.Key, inPublicPool = publicMembers.ContainsKey(member.Key), power, state = "offline", block = (long?)null, lag = (long?)null, trafficReady = (bool?)null };
                var endpoint = $"http://{member.Value}:8545";
                var chain = Height(await Rpc(endpoint, "eth_chainId", []));
                var block = Height(await Rpc(endpoint, "eth_blockNumber", []));
                var common = "0x" + Math.Min(head, block).ToString("x");
                var local = await Rpc(endpoint, "eth_getBlockByNumber", [common, false]);
                var canonical = await Rpc(reference, "eth_getBlockByNumber", [common, false]);
                var hashMatches = local["hash"] is not null && local["hash"]!.GetValue<string>() == canonical["hash"]?.GetValue<string>();
                var synced = chain == Height(networkChain) && Math.Abs(head - block) <= config.GetValue<int>("Fleet:MaxLag", 3) && hashMatches;
                return (object)new { name = member.Key, inPublicPool = publicMembers.ContainsKey(member.Key), power, state = synced ? "synced" : "behind or divergent", block = (long?)block, lag = (long?)(head - block), trafficReady = await TrafficReady(member.Value) };
            } catch { return (object)new { name = member.Key, inPublicPool = publicMembers.ContainsKey(member.Key), power = "unknown", state = "unreachable", block = (long?)null, lag = (long?)null, trafficReady = (bool?)null }; }
        }));
        return new { referenceBlock = head, referenceRpc = reference, observedAt = DateTimeOffset.UtcNow, nodes };
    }
}
