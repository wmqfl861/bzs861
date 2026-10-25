#include "bzs/util.hpp"
#include "bzs/json.hpp"
#include <iostream>
#include <string>
int main(){std::string s;while(std::getline(std::cin,s)){auto j=bzs::Json::object();const auto k=bzs::deletion_url_key(s);j.set("input",s);j.set("key",k);j.set("idempotent",bzs::deletion_url_key(k)==k);std::cout<<j.canonical()<<'\n';}}
