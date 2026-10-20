#define main original_review_unused_main
#include "review_refresh_order.cpp"
#undef main
namespace {
struct LegacyTruncated:Reader {
  Json call(const std::string& method,const Json& params,int timeout) override {
    auto response=Reader::call(method,params,timeout);response.at("warnings").push(Json::string("download_truncated"));return response;
  }
};
}
int main(){try{
  Temporary temp;Store store(temp.path/"state");Reader warm;WebService before(store,warm,{});
  warm.index=false;warm.snippets=false;before.read("alice",args(a));
  check(find_url(hits(store,freeze(store)),a).is_null(),"noindex preparation failed");
  LegacyTruncated incomplete;incomplete.omit_publication=true;WebService old_worker(store,incomplete,{});
  try{old_worker.read("alice",args(a));}catch(const Error& error){check(error.code=="E_WORKER_PROTOCOL","unexpected error "+error.code);}
  check(find_url(hits(store,freeze(store)),a).is_null(),"unversioned download_truncated artifact cleared current noindex");
  std::cout<<"PASS incomplete compatibility artifact cannot relax publication\n";return 0;
}catch(const std::exception& error){std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}}
