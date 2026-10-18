#include "bzs/html.hpp"
#include "bzs/error.hpp"
#include "bzs/util.hpp"
#include <libxml/HTMLparser.h>
#include <libxml/tree.h>
#include <algorithm>
#include <memory>
#include <set>
namespace bzs {
  namespace {
    std::string tag(xmlNode* n) {
      return n&&n->name?ascii_lower(reinterpret_cast<const char*>(n->name)):"";
    }
    std::string prop(xmlNode*n, const char*k) {
      xmlChar* v=xmlGetProp(n, reinterpret_cast<const xmlChar*>(k));
      if(!v)return {
      };
      std::string s(reinterpret_cast<const char*>(v));
      xmlFree(v);
      return s;
    }
    std::string content(xmlNode*n) {
      xmlChar* v=xmlNodeGetContent(n);
      if(!v)return {
      };
      std::string s(reinterpret_cast<const char*>(v));
      xmlFree(v);
      return s;
    }
    bool skip_element(xmlNode*n) {
      static const std::set<std::string> skip= {
        "head", "script", "style", "nav", "footer", "header", "noscript", "iframe", "object", "embed", "svg", "template", "aside", "button", "select"
      };
      auto style=ascii_lower(prop(n, "style"));
      style.erase(std::remove(style.begin(), style.end(), ' '), style.end());
      return skip.contains(tag(n))||xmlHasProp(n, reinterpret_cast<const xmlChar*>("hidden"))||
        ascii_lower(prop(n, "aria-hidden"))=="true"||style.find("display:none")!=std::string::npos||
        style.find("visibility:hidden")!=std::string::npos;
    }
    xmlNode* locate(xmlNode*n, const std::string& name, std::size_t& visited, bool visible_only=false, int depth=0) {
      require(depth<=128, "E_PARSE_LIMIT", "HTML traversal budget exceeded", 413);
      for(auto*p=n; p; p=p->next) {
        require(++visited<=50000, "E_PARSE_LIMIT", "HTML traversal budget exceeded", 413);
        if(visible_only&&p->type==XML_ELEMENT_NODE&&skip_element(p))continue;
        if(p->type==XML_ELEMENT_NODE&&tag(p)==name)return p;
        if(p->children)if(auto*c=locate(p->children, name, visited, visible_only, depth+1))return c;
      }
      return nullptr;
    }
    void newline(std::string& s) {
      while(!s.empty()&&s.back()==' ')s.pop_back();
      if(!s.empty()&&s.back()!='\n')s+='\n';
    }
    void text_append(std::string& s, std::string_view text, bool pre) {
      for(char c:text) {
        if(c=='\r')continue;
        if(pre) {
          s+=c;
          continue;
        }
        if(c=='\n'||c=='\t'||c==' ') {
          if(!s.empty()&&s.back()!=' '&&s.back()!='\n')s+=' ';
        }else s+=c;
      }
      require(s.size()<=1024*1024, "E_CONTENT_TOO_LARGE", "Extracted content too large", 413);
    }
    void walk_children(xmlNode* first, ParsedDocument& out, std::size_t& visited, int depth, bool pre);
    // Visits exactly one chosen node and its descendants, never its siblings.
    void walk_node(xmlNode* p, ParsedDocument& out, std::size_t& visited, int depth=0, bool pre=false) {
      if(!p)return;
      require(depth<=128, "E_PARSE_LIMIT", "HTML nesting too deep", 413);
      static const std::set<std::string> blocks= {
        "p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "ul", "ol", "pre", "blockquote", "br"
      };
      require(++visited<=50000, "E_PARSE_LIMIT", "HTML node budget exceeded", 413);
      if(p->type==XML_TEXT_NODE||p->type==XML_CDATA_SECTION_NODE) {
        if(p->content)text_append(out.text, reinterpret_cast<const char*>(p->content), pre);
        return;
      }
      if(p->type!=XML_ELEMENT_NODE||skip_element(p))return;
      auto name=tag(p);
      bool block=blocks.contains(name);
      if(block)newline(out.text);
      if(name=="a"&&out.links.size()<512) {
        auto h=prop(p, "href");
        if(!h.empty()&&h.size()<=4096&&h.find('\0')==std::string::npos)out.links.push(Json::string(h));
      }
      if(name=="td"||name=="th") {
        if(!out.text.empty()&&out.text.back()!='\n')out.text+=" | ";
      }
      if(p->children)walk_children(p->children, out, visited, depth+1, pre||name=="pre"||name=="code");
      if(block)newline(out.text);
    }
    void walk_children(xmlNode* first, ParsedDocument& out, std::size_t& visited, int depth, bool pre) {
      for(auto*p=first; p; p=p->next)walk_node(p, out, visited, depth, pre);
    }
    void scan_meta(xmlNode*n, ParsedDocument&out, std::size_t& visited, int depth=0) {
      require(depth<=128, "E_PARSE_LIMIT", "Metadata nesting too deep", 413);
      for(auto*p=n; p; p=p->next) {
        require(++visited<=50000, "E_PARSE_LIMIT", "Metadata node budget exceeded", 413);
        if(p->type==XML_ELEMENT_NODE&&tag(p)=="meta"&&ascii_lower(prop(p, "name"))=="robots") {
          auto v=ascii_lower(prop(p, "content"));
          std::size_t start=0;
          while(start<v.size()) {
            auto end=v.find_first_of(", \t\r\n\f\v", start);
            auto token=v.substr(start, end==std::string::npos?v.size()-start:end-start);
            if(token=="noindex"||token=="none")out.index_allowed=false;
            if(end==std::string::npos)break;
            start=end+1;
          }
        }
        if(p->children)scan_meta(p->children, out, visited, depth+1);
      }
    }
  }
  ParsedDocument parse_document(std::string_view input, const std::string& type, const std::string& fallback) {
    require(!input.empty()&&input.size()<=1024*1024, "E_CONTENT_TOO_LARGE", "Document size outside 1..1048576 bytes", 413);
    require(valid_utf8(input)&&input.find('\0')==std::string_view::npos, "E_UTF8", "Document must be UTF-8 without NUL");
    ParsedDocument out;
    out.content_type=type;
    out.title=fallback;
    if(type=="text/plain") {
      out.text=std::string(input);
      out.parser_version="bootstrap-text/1";
    } else {
      require(type=="text/html", "E_CAPABILITY_UNAVAILABLE", "Only UTF-8 text/html and text/plain are supported", 422);
      auto lower=ascii_lower(input);
      require(lower.find("<!entity")==std::string::npos, "E_ACCESS_RESTRICTED", "Entity declarations are unsupported", 422);
      auto doctype=lower.find("<!doctype");
      if(doctype!=std::string::npos) {
        auto end=lower.find('>', doctype);
        auto declaration=lower.substr(doctype, end==std::string::npos?lower.size()-doctype:end-doctype);
        for(char&c:declaration)if(c=='\t'||c=='\r'||c=='\n')c=' ';
        require(declaration.find(" system ")==std::string::npos&&declaration.find(" public ")==std::string::npos, "E_ACCESS_RESTRICTED", "External declarations are unsupported", 422);
      }
      constexpr int options=HTML_PARSE_NONET|HTML_PARSE_NOERROR|HTML_PARSE_NOWARNING|HTML_PARSE_COMPACT;
      std::unique_ptr<xmlDoc, decltype(&xmlFreeDoc)> doc(htmlReadMemory(input.data(), static_cast<int>(input.size()), nullptr, "UTF-8", options), xmlFreeDoc);
      require(static_cast<bool>(doc), "E_PARSE_FAILED", "Cannot parse HTML", 422);
      auto root=xmlDocGetRootElement(doc.get());
      std::size_t visited=0;
      if(auto*t=locate(root, "title", visited))out.title=content(t);
      visited=0;
      scan_meta(root, out, visited);
      visited=0;
      auto chosen=locate(root, "main", visited, true);
      if(!chosen) {
        visited=0;
        chosen=locate(root, "article", visited, true);
      }
      if(!chosen) {
        visited=0;
        chosen=locate(root, "body", visited, true);
      }
      if(!chosen)chosen=root;
      visited=0;
      walk_node(chosen, out, visited);
    }
    while(!out.text.empty()&&(out.text.back()=='\n'||out.text.back()==' '))out.text.pop_back();
    require(!out.text.empty(), "E_PARSE_FAILED", "No readable text", 422);
    out.title=utf8_prefix(out.title, 1024);
    if(out.title.empty())out.title="Untitled";
    require(valid_utf8(out.text)&&valid_utf8(out.title), "E_UTF8", "Parser output is not valid UTF-8", 422);
    return out;
  }
}
