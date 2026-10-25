// Preserve v1 source and add an integrity diagnostic on the registered Core DB.
#define main migration_probe_v1_main
#include "migration_probe.cpp"
#undef main
int main(int argc,char** argv) {
  if(argc==3&&std::string(argv[1])=="integrity") {
    try {
      Store store(argv[2],[]{return std::int64_t{1000000};});
      sqlite3_stmt* raw=nullptr;VERIFY(sqlite3_prepare_v2(store.raw_handle(),"PRAGMA integrity_check",-1,&raw,nullptr)==SQLITE_OK);
      std::unique_ptr<sqlite3_stmt,decltype(&sqlite3_finalize)> statement(raw,sqlite3_finalize);
      VERIFY(sqlite3_step(raw)==SQLITE_ROW);
      VERIFY(std::string(reinterpret_cast<const char*>(sqlite3_column_text(raw,0)))=="ok");
      VERIFY(sqlite3_step(raw)==SQLITE_DONE);std::cout<<"REGISTERED_CORE_INTEGRITY_PASS"<<std::endl;return 0;
    } catch(const std::exception& error) { std::cerr<<"INTEGRITY_PROBE_FAILURE "<<error.what()<<std::endl;return 1; }
  }
  return migration_probe_v1_main(argc,argv);
}
