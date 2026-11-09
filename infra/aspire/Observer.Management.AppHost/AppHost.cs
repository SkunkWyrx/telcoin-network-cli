var builder = DistributedApplication.CreateBuilder(args);
var tenant = builder.AddParameter("entra-tenant-id");
var client = builder.AddParameter("entra-client-id");
var owner = builder.AddParameter("entra-owner-object-id");
var secret = builder.AddParameter("entra-client-secret", secret: true);
builder.AddProject<Projects.Observer_Management>("observer-management")
    .WithEnvironment("Entra__TenantId", tenant)
    .WithEnvironment("Entra__ClientId", client)
    .WithEnvironment("Entra__ClientSecret", secret)
    .WithEnvironment("Entra__AllowedObjectId", owner)
    .WithHttpsEndpoint();
builder.Build().Run();
