using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;

static void Check(bool value, string message) { if (!value) throw new Exception(message); }
Check(AccessPolicy.Matches("*", "Microsoft.Compute/virtualMachines/write"), "Owner wildcard");
Check(AccessPolicy.Matches("Microsoft.Compute/*", "microsoft.compute/virtualMachines/write"), "Case insensitive wildcard");
Check(!AccessPolicy.Matches("*/read", "Microsoft.Compute/virtualMachines/write"), "Readers cannot write");
Check(!AccessPolicy.Matches("Microsoft.Compute/virtualMachines/write", "Microsoft.Compute/virtualMachines/delete"), "Exact rights");
foreach (var o in new[] { new Operation("add", "../secrets"), new Operation("remove", "node; reboot"), new Operation("upgrade", "repo:latest"), new Operation("build", "main"), new Operation("remove", "node\n") })
    Check(!Operation.Valid(o), "Reject unsafe argument: " + o);
Check(Operation.Valid(new("build", "refs/tags/v0.16.0-adiri")), "Full release ref");
Check(Operation.Valid(new("upgrade", "registry.azurecr.io/telcoin-observer/adiri@sha256:" + new string('a', 64))), "Digest pinned image");
var reader = System.Text.Json.Nodes.JsonNode.Parse("""{"value":[{"actions":["*/read"],"notActions":[]}]}""")!;
Check(!AccessPolicy.Allows(reader, "Microsoft.Compute/virtualMachines/write"), "Reader denied");
var union = System.Text.Json.Nodes.JsonNode.Parse("""{"value":[{"actions":["*"],"notActions":["Microsoft.Compute/*"]},{"actions":["Microsoft.Compute/virtualMachines/write"],"notActions":[]}]}""")!;
Check(AccessPolicy.Allows(union, "Microsoft.Compute/virtualMachines/write"), "Another role can grant an excluded action");
Check(!AccessPolicy.Allows(union, "Microsoft.Compute/virtualMachines/delete"), "Excluded action without another grant denied");
Console.WriteLine("14 management policy checks passed.");
Check(!AccessPolicy.IsOwner(null, Guid.NewGuid().ToString()), "Missing owner config denies access");
var owner = Guid.NewGuid().ToString();
Check(AccessPolicy.IsOwner(owner, owner.ToUpperInvariant()), "Owner ID matches");
Check(!AccessPolicy.IsOwner(owner, Guid.NewGuid().ToString()), "Other tenant user denied");
Check(Operation.Valid(new("upgrade-tag", "v0.16.0-adiri")), "Upgrade by tag");
Check(!Operation.Valid(new("upgrade-tag", "main")), "Branch cannot be used as release tag");
Check(!Operation.Valid(new("upgrade-tag", "v1.0.0-adiri\n")), "Tag cannot have trailing newline");
Console.WriteLine("6 additional owner and release-tag checks passed.");
var duplicated = System.Text.Json.Nodes.JsonNode.Parse("""
{"properties":{"backendIPConfigurations":[{"id":"NIC"}],"loadBalancerBackendAddresses":[{"properties":{"networkInterfaceIPConfiguration":{"id":"nic"}}}]}}
""")!;
Check(BackendInventory.NicConfigurationIds(duplicated).Count == 1, "Azure dual NIC representation is deduplicated case-insensitively");
var addressOnly = System.Text.Json.Nodes.JsonNode.Parse("""{"properties":{"loadBalancerBackendAddresses":[{"properties":{"networkInterfaceIPConfiguration":{"id":"NIC"}}}]}}""")!;
Check(BackendInventory.NicConfigurationIds(addressOnly).Single() == "NIC", "NIC references in address collection are discovered");
var legacy = System.Text.Json.Nodes.JsonNode.Parse("""{"properties":{"backendIPConfigurations":[{"id":"NIC"}]}}""")!;
Check(BackendInventory.NicConfigurationIds(legacy).Single() == "NIC", "Legacy NIC collection is discovered");
var ipOnly = System.Text.Json.Nodes.JsonNode.Parse("""{"properties":{"backendIPConfigurations":[{"id":"NIC"}],"loadBalancerBackendAddresses":[{"properties":{"ipAddress":"10.0.0.9"}}]}}""")!;
var rejected = false;
try { BackendInventory.NicConfigurationIds(ipOnly); } catch (InvalidOperationException) { rejected = true; }
Check(rejected, "Mixed IP-only membership cannot silently produce incomplete inventory");
Check(BackendInventory.NicConfigurationIds(System.Text.Json.Nodes.JsonNode.Parse("""{"properties":{}}""")!).Count == 0, "Empty pool is supported");
Console.WriteLine("5 backend inventory checks passed.");
// Exercise the production route registration over HTTP: a successful method
// invocation alone does not prove ASP.NET writes its returned IResult.
var builder = Microsoft.AspNetCore.Builder.WebApplication.CreateBuilder();
builder.WebHost.UseSetting("urls", "http://127.0.0.1:0");
await using var app = builder.Build();
FleetEndpoints.MapStatus(app.MapGroup(""), _ => Task.FromResult<object>(new {
    referenceBlock = 123L, nodes = new[] { new { name = "observer-1", block = 123L }, new { name = "observer-2", block = 122L } }
}));
await app.StartAsync();
try {
    using var http = new HttpClient();
    using var response = await http.GetAsync(app.Urls.Single() + "/api/status");
    Check(response.IsSuccessStatusCode, "Status endpoint succeeds");
    Check(response.Content.Headers.ContentType?.MediaType == "application/json", "Status endpoint writes JSON content type");
    var body = System.Text.Json.Nodes.JsonNode.Parse(await response.Content.ReadAsStringAsync())!;
    Check(body["referenceBlock"]!.GetValue<long>() == 123 && body["nodes"]!.AsArray().Count == 2, "Status endpoint serializes reference and both nodes");
} finally { await app.StopAsync(); }
Console.WriteLine("3 HTTP response checks passed.");
