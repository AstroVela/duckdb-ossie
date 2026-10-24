#ifdef OSSIE_VANE_DISTRIBUTED

#define DUCKDB_EXTENSION_MAIN
#include "ossie_extension.hpp"

#include "duckdb/catalog/catalog.hpp"
#include "duckdb/catalog/catalog_entry/scalar_function_catalog_entry.hpp"
#include "duckdb/catalog/catalog_entry/table_function_catalog_entry.hpp"
#include "duckdb/common/allocator.hpp"
#include "duckdb/function/pragma_function.hpp"
#include "duckdb/main/extension/extension_loader.hpp"
#include "duckdb/parser/tableref/table_function_ref.hpp"
#include "ossie/functions.hpp"

namespace duckdb {

static void LoadCoordinatorModel(ClientContext &context, const FunctionParameters &parameters) {
	// PRAGMA is a coordinator command in Vane. Reuse the original loader on
	// this context so parsing, source validation, rebinding and policy stay
	// identical to CALL ossie_load, which Ray connections reject.
	auto &entry = Catalog::GetEntry<TableFunctionCatalogEntry>(context, SYSTEM_CATALOG, DEFAULT_SCHEMA, "ossie_load");
	auto &function = entry.functions.functions[0];
	auto values = parameters.values;
	auto named_parameters = parameters.named_parameters;
	vector<LogicalType> input_types, return_types;
	vector<string> input_names, return_names;
	TableFunctionRef ref;
	TableFunctionBindInput bind_input(values, named_parameters, input_types, input_names, nullptr, nullptr, function,
	                                  ref);
	auto bind_data = function.bind(context, bind_input, return_types, return_names);
	vector<column_t> column_ids;
	for (idx_t column = 0; column < return_types.size(); column++) {
		column_ids.push_back(column);
	}
	vector<idx_t> projection_ids;
	TableFunctionInitInput init_input(bind_data.get(), std::move(column_ids), projection_ids, nullptr);
	auto global_state = function.init_global(context, init_input);
	DataChunk output;
	output.Initialize(Allocator::Get(context), return_types);
	TableFunctionInput input(bind_data.get(), nullptr, global_state.get());
	function.function(context, input, output);
}

static void LoadVane(ExtensionLoader &loader) {
	ossie::RegisterLoadFunction(loader);
	ossie::RegisterDescribeFunctions(loader);
	ossie::RegisterCompileFunction(loader);
	ossie::RegisterQueryFunction(loader);

	// Loading mutates the coordinator's model cache. It must never become a
	// worker scan or be evaluated while binding EXPLAIN.
	for (auto &function : loader.GetTableFunction("ossie_load").functions.functions) {
		function.SetRequiresClientContext();
	}
	auto load_model = PragmaFunction::PragmaCall("vane_ossie_load", LoadCoordinatorModel, {LogicalType::VARCHAR});
	load_model.named_parameters = loader.GetTableFunction("ossie_load").functions.functions[0].named_parameters;
	loader.RegisterFunction(std::move(load_model));
	// These functions read the coordinator's model and catalog. Vane explicitly
	// executes metadata-only queries against that context and rejects mixing
	// them with ordinary data in a distributed query.
	for (auto name : {"ossie_datasets", "ossie_fields", "ossie_metrics", "ossie_relationships"}) {
		for (auto &function : loader.GetTableFunction(name).functions.functions) {
			function.SetClientContextRead();
		}
	}
	for (auto &function : loader.GetFunction("ossie_compile").functions.functions) {
		function.SetClientContextRead();
	}
	// ossie_query's original bind_replace expands the model into an ordinary
	// SELECT on the coordinator. Only the resulting data plan reaches workers.
}

void OssieExtension::Load(ExtensionLoader &loader) {
	LoadVane(loader);
}

std::string OssieExtension::Name() {
	return "ossie";
}

std::string OssieExtension::Version() const {
	return EXT_VERSION_OSSIE;
}

} // namespace duckdb

extern "C" {
DUCKDB_CPP_EXTENSION_ENTRY(ossie, loader) {
	duckdb::LoadVane(loader);
}
}

#endif
