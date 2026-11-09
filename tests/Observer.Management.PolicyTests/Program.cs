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
